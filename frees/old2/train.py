from functools import partial
import shutil
import numpy as np
from config import Config
import gc
import matplotlib.pyplot as plt
import pandas as pd
import os
from tqdm import tqdm
import torch
import torchvision
import torchvision.transforms as T
import argparse
from tensorboardX import SummaryWriter
from sklearn.model_selection import KFold
from lr_scheduler import OneCycleScheduler
import lr_scheduler_wrapper
from optim import AdamW
import utils
from .model import Model
from .dataset import NUM_CLASSES, ID_TO_CLASS, EPS, TrainEvalDataset, TestDataset, load_train_eval_data, load_test_data
from .utils import collate_fn
from loss import lsep_loss
from frees.transform import RandomCrop, LoadSpectra
from frees.metric import calculate_per_class_lwlrap


# TODO: crop signal, not spectra
# TODO: crop to average curated len
# TODO: rename train_eval to train_curated
# TODO: remove CyclicLR
# TODO: cutout
# TODO: resample silence
# TODO: benchmark stft
# TODO: scipy stft


class MixupDataLoader(object):
    def __init__(self, data_loader, alpha):
        self.data_loader = data_loader
        self.dist = torch.distributions.beta.Beta(alpha, alpha)

        # TODO: handle ids
        # TODO: sample beta for each sample
        # TODO: speedup

    def __iter__(self):
        for (images_1, labels_1, ids_1), (images_2, labels_2, ids_2) \
                in zip(self.data_loader, self.data_loader):
            lam = self.dist.sample().to(DEVICE)

            images = lam * images_1.to(DEVICE) + (1 - lam) * images_2.to(DEVICE)
            labels = lam * labels_1.to(DEVICE) + (1 - lam) * labels_2.to(DEVICE)
            ids = ids_1 if lam > 0.5 else ids_2

            yield images, labels, ids

    def __len__(self):
        return len(self.data_loader)


# TODO: try max pool
# TODO: sgd
# TODO: check del
# TODO: try largest lr before diverging
# TODO: check all plots rendered
# TODO: adamw

FOLDS = list(range(1, 5 + 1))

parser = argparse.ArgumentParser()
parser.add_argument('--config-path', type=str, required=True)
parser.add_argument('--experiment-path', type=str, default='./tf_log/frees')
parser.add_argument('--dataset-path', type=str, required=True)
parser.add_argument('--workers', type=int, default=os.cpu_count())
parser.add_argument('--fold', type=int, choices=FOLDS)
parser.add_argument('--debug', action='store_true')
args = parser.parse_args()
config = Config.from_yaml(args.config_path)
shutil.copy(args.config_path, utils.mkdir(args.experiment_path))

DEVICE = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
MEAN, STD = np.load(os.path.join(args.dataset_path, 'stats.npy'))

if config.pad == 'silence':
    PAD_VALUE = (np.log(EPS) - MEAN) / STD
elif config.pad == 'zeros':
    PAD_VALUE = 0.
else:
    raise AssertionError('invalid pad {}'.format(config.pad))

to_tensor_and_norm = T.Compose([
    T.ToTensor(),
    T.Normalize(mean=[MEAN], std=[STD])
])

if config.aug.type == 'pad':
    train_transform = T.Compose([
        LoadSpectra(augmented=True),
        to_tensor_and_norm,
    ])
    test_transform = eval_transform = T.Compose([
        LoadSpectra(),
        to_tensor_and_norm,
    ])
elif config.aug.type == 'crop':
    train_transform = T.Compose([
        LoadSpectra(augmented=True),
        RandomCrop(config.image_size),
        to_tensor_and_norm,
    ])
    test_transform = eval_transform = T.Compose([
        LoadSpectra(),
        to_tensor_and_norm,
    ])
else:
    raise AssertionError('invalid aug {}'.format(config.aug.type))


def compute_loss(input, target):
    loss = lsep_loss(input=input, target=target)
    assert loss.dim() == 0

    return loss


def compute_score(input, target):
    per_class_lwlrap, weight_per_class = calculate_per_class_lwlrap(
        truth=target.data.cpu().numpy(), scores=input.data.cpu().numpy())

    return np.sum(per_class_lwlrap * weight_per_class)


def get_nrow(images):
    h = images.size(0) * images.size(2)
    w = images.size(3)

    a = h * w
    k = np.sqrt(a / 2)

    nrow = a / (2 * k) / w
    nrow = np.ceil(nrow).astype(np.int32)

    return nrow


# TODO: pin memory
# TODO: stochastic weight averaging
# TODO: group images by buckets (size, ratio) and batch
# TODO: hinge loss clamp instead of minimum
# TODO: losses
# TODO: better one cycle
# TODO: cos vs lin
# TODO: load and restore state after lr finder
# TODO: better loss smoothing
# TODO: shuffle thresh search
# TODO: init thresh search from global best
# TODO: shuffle split
# TODO: tune on large size
# TODO: cross val
# TODO: smart sampling
# TODO: larger model
# TODO: imagenet papers
# TODO: load image as jpeg
# TODO: augmentations (flip, crops, color)
# TODO: min 1 tag?
# TODO: pick threshold to match ratio
# TODO: compute smoothing beta from batch size and num steps
# TODO: speedup image loading
# TODO: pin memory
# TODO: smart sampling
# TODO: better threshold search (step, epochs)
# TODO: weight standartization
# TODO: label smoothing
# TODO: build sched for lr find


# TODO: should use top momentum to pick best lr?
def build_optimizer(optimizer, parameters, lr, beta, weight_decay):
    if optimizer == 'adam':
        return torch.optim.Adam(parameters, lr, betas=(beta, 0.999), weight_decay=weight_decay)
    elif optimizer == 'adamw':
        return AdamW(parameters, lr, betas=(beta, 0.999), weight_decay=weight_decay)
    elif optimizer == 'momentum':
        return torch.optim.SGD(parameters, lr, momentum=beta, weight_decay=weight_decay, nesterov=True)
    else:
        raise AssertionError('invalid OPT {}'.format(optimizer))


def indices_for_fold(fold, dataset_size):
    kfold = KFold(len(FOLDS), shuffle=True, random_state=config.seed)
    splits = list(kfold.split(np.zeros(dataset_size)))
    train_indices, eval_indices = splits[fold - 1]
    assert len(train_indices) + len(eval_indices) == dataset_size

    return train_indices, eval_indices


def find_lr(train_eval_data, train_noisy_data):
    train_eval_dataset = torch.utils.data.ConcatDataset([
        TrainEvalDataset(train_eval_data, transform=train_transform),
        TrainEvalDataset(train_noisy_data, transform=train_transform)
    ])

    # TODO: all args
    train_eval_data_loader = torch.utils.data.DataLoader(
        train_eval_dataset,
        batch_size=config.batch_size,
        drop_last=True,
        shuffle=True,
        num_workers=args.workers,
        collate_fn=partial(collate_fn, pad_value=PAD_VALUE))
    if config.mixup is not None:
        train_eval_data_loader = MixupDataLoader(train_eval_data_loader, config.mixup)

    min_lr = 1e-7
    max_lr = 10.
    gamma = (max_lr / min_lr)**(1 / len(train_eval_data_loader))

    lrs = []
    losses = []
    lim = None

    model = Model(config.model, NUM_CLASSES)
    model = model.to(DEVICE)
    optimizer = build_optimizer(
        config.opt.type, model.parameters(), min_lr, config.opt.beta, weight_decay=config.opt.weight_decay)
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma)

    model.train()
    for images, labels, ids in tqdm(train_eval_data_loader, desc='lr search'):
        images, labels = images.to(DEVICE), labels.to(DEVICE)
        logits, _ = model(images)

        loss = compute_loss(input=logits, target=labels)

        lrs.append(np.squeeze(scheduler.get_lr()))
        losses.append(loss.data.cpu().numpy().mean())

        if lim is None:
            lim = losses[0] * 1.1

        if lim < losses[-1]:
            break

        optimizer.zero_grad()
        loss.mean().backward()
        optimizer.step()
        scheduler.step()

        if args.debug:
            break

    with torch.no_grad():
        losses = np.clip(losses, 0, lim)
        minima_loss = losses[np.argmin(utils.smooth(losses))]
        minima_lr = lrs[np.argmin(utils.smooth(losses))]

        writer = SummaryWriter(os.path.join(args.experiment_path, 'lr_search'))

        step = 0
        for loss, loss_sm in zip(losses, utils.smooth(losses)):
            writer.add_scalar('search_loss', loss, global_step=step)
            writer.add_scalar('search_loss_sm', loss_sm, global_step=step)
            step += config.batch_size

        plt.plot(lrs, losses)
        plt.plot(lrs, utils.smooth(losses))
        plt.axvline(minima_lr)
        plt.xscale('log')
        plt.title('loss: {:.8f}, lr: {:.8f}'.format(minima_loss, minima_lr))
        plot = utils.plot_to_image()
        writer.add_image('search', plot.transpose((2, 0, 1)), global_step=0)

        return minima_lr


def train_epoch(model, optimizer, scheduler, data_loader, fold, epoch):
    writer = SummaryWriter(os.path.join(args.experiment_path, 'fold{}'.format(fold), 'train'))

    metrics = {
        'loss': utils.Mean(),
    }

    model.train()
    for images, labels, ids in tqdm(data_loader, desc='epoch {} train'.format(epoch)):
        images, labels = images.to(DEVICE), labels.to(DEVICE)
        logits, weights = model(images)

        loss = compute_loss(input=logits, target=labels)
        metrics['loss'].update(loss.data.cpu().numpy())

        optimizer.zero_grad()
        loss.mean().backward()
        optimizer.step()
        scheduler.step()

        if args.debug:
            break

    with torch.no_grad():
        loss = metrics['loss'].compute_and_reset()

        print('[FOLD {}][EPOCH {}][TRAIN] loss: {:.4f}'.format(fold, epoch, loss))
        writer.add_scalar('loss', loss, global_step=epoch)
        lr, beta = scheduler.get_lr()
        writer.add_scalar('learning_rate', lr, global_step=epoch)
        writer.add_scalar('beta', beta, global_step=epoch)
        writer.add_image(
            'image',
            torchvision.utils.make_grid(images[:32], nrow=get_nrow(images[:32]), normalize=True),
            global_step=epoch)
        writer.add_image(
            'weights',
            torchvision.utils.make_grid(weights[:32], nrow=get_nrow(weights[:32])),
            global_step=epoch)


def eval_epoch(model, data_loader, fold, epoch):
    writer = SummaryWriter(os.path.join(args.experiment_path, 'fold{}'.format(fold), 'eval'))

    metrics = {
        'loss': utils.Mean(),
    }

    predictions = []
    targets = []
    model.eval()
    with torch.no_grad():
        for images, labels, ids in tqdm(data_loader, desc='epoch {} evaluation'.format(epoch)):
            images, labels = images.to(DEVICE), labels.to(DEVICE)
            logits, weights = model(images)

            targets.append(labels)
            predictions.append(logits)

            loss = compute_loss(input=logits, target=labels)
            metrics['loss'].update(loss.data.cpu().numpy())

            if args.debug:
                break

        loss = metrics['loss'].compute_and_reset()

        predictions = torch.cat(predictions, 0)
        targets = torch.cat(targets, 0)
        score = compute_score(input=predictions, target=targets)

        print('[FOLD {}][EPOCH {}][EVAL] loss: {:.4f}, score: {:.4f}'.format(fold, epoch, loss, score))
        writer.add_scalar('loss', loss, global_step=epoch)
        writer.add_scalar('score', score, global_step=epoch)
        writer.add_image(
            'image',
            torchvision.utils.make_grid(images[:32], nrow=get_nrow(images[:32]), normalize=True),
            global_step=epoch)
        writer.add_image(
            'weights',
            torchvision.utils.make_grid(weights[:32], nrow=get_nrow(weights[:32])),
            global_step=epoch)

        return score


def train_fold(fold, train_eval_data, train_noisy_data, lr):
    train_indices, eval_indices = indices_for_fold(fold, len(train_eval_data))

    train_dataset = torch.utils.data.ConcatDataset([
        TrainEvalDataset(train_eval_data.iloc[train_indices], transform=train_transform),
        TrainEvalDataset(train_noisy_data, transform=train_transform)
    ])
    train_data_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=config.batch_size,
        drop_last=True,
        shuffle=True,
        num_workers=args.workers,
        collate_fn=partial(collate_fn, pad_value=PAD_VALUE))  # TODO: all args
    if config.mixup is not None:
        train_data_loader = MixupDataLoader(train_data_loader, config.mixup)

    eval_dataset = TrainEvalDataset(train_eval_data.iloc[eval_indices], transform=eval_transform)
    eval_data_loader = torch.utils.data.DataLoader(
        eval_dataset,
        batch_size=config.batch_size,
        num_workers=args.workers,
        collate_fn=partial(collate_fn, pad_value=PAD_VALUE))  # TODO: all args

    model = Model(config.model, NUM_CLASSES)
    model = model.to(DEVICE)
    optimizer = build_optimizer(
        config.opt.type, model.parameters(), lr, config.opt.beta, weight_decay=config.opt.weight_decay)

    if config.sched.type == 'onecycle':
        scheduler = lr_scheduler_wrapper.StepWrapper(
            OneCycleScheduler(
                optimizer,
                lr=(lr / 20, lr),
                beta=config.sched.onecycle.beta,
                max_steps=len(train_data_loader) * config.epochs,
                annealing=config.sched.onecycle.anneal))
    elif config.sched.type == 'cyclic':
        scheduler = lr_scheduler_wrapper.StepWrapper(
            torch.optim.lr_scheduler.CyclicLR(
                optimizer,
                0.,
                lr,
                step_size_up=len(train_data_loader),
                step_size_down=len(train_data_loader),
                mode='triangular2',
                cycle_momentum=True,
                base_momentum=config.sched.cyclic.beta[1],
                max_momentum=config.sched.cyclic.beta[0]))
    elif config.sched.type == 'cawr':
        scheduler = lr_scheduler_wrapper.StepWrapper(
            torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
                optimizer, T_0=len(train_data_loader), T_mult=2))
    elif config.sched.type == 'plateau':
        scheduler = lr_scheduler_wrapper.ScoreWrapper(
            torch.optim.lr_scheduler.ReduceLROnPlateau(
                optimizer, mode='max', factor=0.5, patience=0, verbose=True))
    else:
        raise AssertionError('invalid sched {}'.format(config.sched.type))

    best_score = 0
    for epoch in range(config.epochs):

        train_epoch(
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
            data_loader=train_data_loader,
            fold=fold,
            epoch=epoch)
        gc.collect()
        score = eval_epoch(
            model=model,
            data_loader=eval_data_loader,
            fold=fold,
            epoch=epoch)
        gc.collect()

        scheduler.step_epoch()
        scheduler.step_score(score)

        if score > best_score:
            best_score = score
            torch.save(model.state_dict(), os.path.join(args.experiment_path, 'model_{}.pth'.format(fold)))


def build_submission(folds, test_data):
    with torch.no_grad():
        predictions = 0.

        for fold in folds:
            fold_predictions, fold_ids = predict_on_test_using_fold(fold, test_data)
            predictions = predictions + fold_predictions.sigmoid()
            ids = fold_ids

        predictions = predictions / len(folds)

        return predictions, ids


def predict_on_test_using_fold(fold, test_data):
    test_dataset = TestDataset(test_data, transform=eval_transform)
    test_data_loader = torch.utils.data.DataLoader(
        test_dataset,
        batch_size=config.batch_size,
        num_workers=args.workers,
        collate_fn=partial(collate_fn, pad_value=PAD_VALUE))  # TODO: all args

    model = Model(config.model, NUM_CLASSES)
    model = model.to(DEVICE)
    model.load_state_dict(torch.load(os.path.join(args.experiment_path, 'model_{}.pth'.format(fold))))

    model.eval()
    with torch.no_grad():
        fold_predictions = []
        fold_ids = []
        for images, ids in tqdm(test_data_loader, desc='fold {} inference'.format(fold)):
            images = images.to(DEVICE)
            logits, _ = model(images)
            fold_predictions.append(logits)
            fold_ids.extend(ids)

            if args.debug:
                break

        fold_predictions = torch.cat(fold_predictions, 0)

    return fold_predictions, fold_ids


def predict_on_eval_using_fold(fold, train_eval_data):
    _, eval_indices = indices_for_fold(fold, len(train_eval_data))

    eval_dataset = TrainEvalDataset(train_eval_data.iloc[eval_indices], transform=eval_transform)
    eval_data_loader = torch.utils.data.DataLoader(
        eval_dataset,
        batch_size=config.batch_size,
        num_workers=args.workers,
        collate_fn=partial(collate_fn, pad_value=PAD_VALUE))  # TODO: all args

    model = Model(config.model, NUM_CLASSES)
    model = model.to(DEVICE)
    model.load_state_dict(torch.load(os.path.join(args.experiment_path, 'model_{}.pth'.format(fold))))

    model.eval()
    with torch.no_grad():
        fold_targets = []
        fold_predictions = []
        fold_ids = []
        for images, labels, ids in tqdm(eval_data_loader, desc='fold {} best model evaluation'.format(fold)):
            images, labels = images.to(DEVICE), labels.to(DEVICE)
            logits, _ = model(images)

            fold_targets.append(labels)
            fold_predictions.append(logits)
            fold_ids.extend(ids)

            if args.debug:
                break

        fold_targets = torch.cat(fold_targets, 0)
        fold_predictions = torch.cat(fold_predictions, 0)

    return fold_targets, fold_predictions, fold_ids


def evaluate_folds(folds, train_eval_data):
    with torch.no_grad():
        targets = []
        predictions = []
        for fold in folds:
            fold_targets, fold_predictions, fold_ids = predict_on_eval_using_fold(fold, train_eval_data)
            targets.append(fold_targets)
            predictions.append(fold_predictions)

        # TODO: check aggregated correctly
        predictions = torch.cat(predictions, 0)
        targets = torch.cat(targets, 0)
        score = compute_score(input=predictions, target=targets)

        print('score: {:.4f}'.format(score))


# TODO: check FOLDS usage


def main():
    # TODO: refactor seed
    utils.seed_everything(config.seed)

    train_eval_data = load_train_eval_data(args.dataset_path, 'train_curated')
    train_noisy_data = load_train_eval_data(args.dataset_path, 'train_noisy')
    test_data = load_test_data(args.dataset_path, 'test')

    for data in [train_eval_data, train_noisy_data, test_data]:
        if args.debug:
            data['path'] = './frees/sample.wav'

    if config.opt.lr is None:
        lr = find_lr(train_eval_data, train_noisy_data)
        gc.collect()

    else:
        lr = config.opt.lr

    if args.fold is None:
        folds = FOLDS
    else:
        folds = [args.fold]

    for fold in folds:
        train_fold(fold, train_eval_data, train_noisy_data, lr)

    # TODO: check and refine
    # TODO: remove?
    evaluate_folds(folds, train_eval_data)
    predictions, ids = build_submission(folds, test_data)
    predictions = predictions.cpu()
    submission = {
        'fname': ids,
        **{ID_TO_CLASS[i]: predictions[:, i] for i in range(NUM_CLASSES)}
    }
    submission = pd.DataFrame(submission)
    submission.to_csv(os.path.join(args.experiment_path, 'submission.csv'), index=False)


if __name__ == '__main__':
    main()