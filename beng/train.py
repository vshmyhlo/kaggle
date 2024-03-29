import glob
import importlib.util
import math
import os

import click
import matplotlib.pyplot as plt
import numpy as np
import sklearn
import torch
import torch.utils.data
import torchvision
import torchvision.transforms as T
from PIL import Image
from iterstrat.ml_stratifiers import MultilabelStratifiedKFold
from tensorboardX import SummaryWriter
from tqdm import tqdm

import utils
from all_the_tools.metrics import Last, Mean, Metric, Concat
from all_the_tools.torch.losses import softmax_cross_entropy
from all_the_tools.torch.optim import LookAhead
from all_the_tools.torch.utils import Saver
from all_the_tools.utils import seed_python
from beng.dataset import LabeledDataset, load_labeled_data, split_target, CLASS_META, decode_target, IMAGE_SIZE
from beng.model import Model
from beng.transforms import Invert
from lr_scheduler import OneCycleScheduler, CosineWithWarmup
from transforms import Resettable

# TODO: maybe clip bad losses
# TODO: ewa of loss/wights and use it to reweight
# TODO: seresnext
# TODO: predict full character
# TODO: segment and classify
# TODO: synthetic data
# TODO: reweight loss based on how bad sample is
# TODO: cutout
# TODO: filter-response normalization
# TODO: train multiple models and make noisy student/teacher
# TODO: ewa optimization
# TODO: tf_efficientnet_b1_ns
# TODO: random crops
# TODO: progressive crops
# TODO: remove bad samples
# TODO: crop sides
# TODO: dropout
# TODO: pseudo label
# TODO: mixmatch
# TODO: do not use OHEM samples
# TODO: median ohem
# TODO: online hard examples mininig
# TODO: make label smoothing scale with loss
# TODO: label smoothing
# TODO: focal, weighting, lsep
# TODO: no soft recall
# TODO: self-mixup, self-cutmix
# TODO: population based training
# TODO: mixmatch
# TODO: stn regularize to identity
# TODO: pl
# TODO: spatial transformer network !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# TODO: dropout
# TODO: strat split
# TODO: postprocess coocurence
# TODO: manifold mixup
# TODO: no soft f1 when trained with self-dist or LS
# TODO: nonlinear heads for classes
# TODO: lsep loss
# TODO: cutmix: progressive
# TODO: distillation
# TODO: erosion/dilation
# TODO: perspective
# TODO: shear/scale and other affine


# TODO: skeletonize and add noize
# TODO: scale
# TODO: mixmatch
# TODO: shift
# TODO: per-component metric
# TODO: center images
# TODO: synthetic data
# TODO: ohem
# TODO: ewa over distillation targets
# TODO: ewa over distillation mixing coefficient


DEVICE = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')


class HMAR(Metric):
    def compute(self):
        input = np.concatenate(self.input, 0)
        target = np.concatenate(self.target, 0)

        scores = [
            sklearn.metrics.recall_score(target[..., i], input[..., i], average='macro')
            for i in range(input.shape[-1])
        ]

        hmar = np.average(scores, weights=CLASS_META['weight'].values)

        return hmar

    def update(self, input, target):
        self.input.append(input)
        self.target.append(target)

    def reset(self):
        self.input = []
        self.target = []


def compute_weight_from_loss(loss):
    weight = 1 - 1 / (1 + loss)
    weight = weight / weight.mean()

    return weight


def compute_nrow(images):
    b, _, h, w = images.size()
    nrow = math.ceil(math.sqrt(h * b / w))

    return nrow


def weighted_sum(a, b, w):
    return w * a + (1 - w) * b


def worker_init_fn(_):
    seed_python(torch.initial_seed() % 2**32)


def softmax_recall_loss(input, target, dim=-1, eps=1e-7):
    assert input.dim() == 2
    assert input.size() == target.size()

    input = input.softmax(dim)

    tp = (target * input).sum()
    fn = (target * (1 - input)).sum()
    r = tp / (tp + fn + eps)

    loss = 1 - r

    return loss


def softmax_entropy(input, dim=-1):
    prob = input.softmax(dim)
    log_prob = input.log_softmax(dim)

    return -(prob * log_prob).sum(dim)


def compute_entropy(input):
    def compute(input, dim=-1):
        return softmax_entropy(input, dim=dim)

    input = split_target(input)
    entropy = [compute(i) for i in input]
    entropy = torch.stack(entropy, -1)

    return entropy


def compute_loss(input, target, config):
    def compute(input, target, dim=-1):
        if config.label_smoothing is not None:
            target = utils.label_smoothing(target, eps=config.label_smoothing)

        ce = softmax_cross_entropy(input=input, target=target, axis=dim)
        r = softmax_recall_loss(input=input, target=target, dim=dim)

        loss = weighted_sum(ce.mean(), r.mean(), 0.5)

        return loss

    input = split_target(input)
    target = split_target(target)

    loss = [compute(input=i, target=t) for i, t in zip(input, target)]
    assert len(loss) == len(CLASS_META['weight'].values)
    loss = sum([l * w for l, w in zip(loss, CLASS_META['weight'].values)])

    return loss


def indices_for_fold(data, fold, seed):
    kfold = MultilabelStratifiedKFold(5, shuffle=True, random_state=seed)
    targets = data[CLASS_META['component']].values
    split = list(kfold.split(data, targets))[fold]

    return split


def build_optimizer(parameters, config):
    if config.type == 'sgd':
        optimizer = torch.optim.SGD(
            parameters,
            config.lr,
            momentum=config.momentum,
            weight_decay=config.weight_decay,
            nesterov=True)
    elif config.type == 'rmsprop':
        optimizer = torch.optim.RMSprop(
            parameters,
            config.lr,
            momentum=config.momentum,
            weight_decay=config.weight_decay)
    elif config.type == 'adam':
        optimizer = torch.optim.Adam(
            parameters,
            config.lr,
            weight_decay=config.weight_decay)
    else:
        raise AssertionError('invalid optimizer {}'.format(config.type))

    if config.lookahead is not None:
        optimizer = LookAhead(
            optimizer,
            lr=config.lookahead.lr,
            num_steps=config.lookahead.steps)

    # if config.ewa is not None:
    #     optimizer = optim.EWA(
    #         optimizer,
    #         config.ewa.momentum,
    #         num_steps=config.ewa.steps)
    # else:
    #     optimizer = optim.DummySwitchable(optimizer)

    return optimizer


def build_scheduler(optimizer, config, optimizer_config, epochs, steps_per_epoch):
    if config.type == 'cosine':
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, epochs * steps_per_epoch)
    elif config.type == 'onecycle':
        scheduler = OneCycleScheduler(
            optimizer,
            min_lr=optimizer_config.lr / 100,
            beta_range=[0.95, 0.85],
            max_steps=epochs * steps_per_epoch,
            annealing='linear',
            peak_pos=0.45,
            end_pos=0.9)
    elif config.type == 'coswarm':
        scheduler = CosineWithWarmup(
            optimizer,
            warmup_steps=steps_per_epoch,
            max_steps=epochs * steps_per_epoch)
    elif config.type == 'step':
        scheduler = torch.optim.lr_scheduler.StepLR(
            optimizer,
            step_size=(epochs * steps_per_epoch) // 3,
            gamma=0.1)
    else:
        raise AssertionError('invalid scheduler {}'.format(config.type))

    return scheduler


def load_config(config_path, **kwargs):
    spec = importlib.util.spec_from_file_location('config', config_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module.config
    for k in kwargs:
        setattr(config, k, kwargs[k])

    return config


def build_transforms():
    def update_transforms(p):
        crop_size = tuple([
            round(size // 2 + (size - size // 2) * p)
            for size in IMAGE_SIZE])

        random_crop.reset(crop_size)
        center_crop.reset(crop_size)

        print('update transforms p: {:.2f}, crop_size: {}'.format(p, crop_size))

    random_crop = Resettable(T.RandomCrop)
    center_crop = Resettable(T.CenterCrop)

    to_tensor_and_norm = T.Compose([
        T.ToTensor(),
        T.Normalize(
            np.mean((0.485, 0.456, 0.406), keepdims=True),
            np.mean((0.229, 0.224, 0.225), keepdims=True)),
    ])
    train_transform = T.Compose([
        Invert(),
        T.RandomAffine(degrees=15, scale=(0.8, 1 / 0.8), resample=Image.BILINEAR),
        random_crop,
        to_tensor_and_norm,
        # T.RandomErasing(value=0),
    ])
    eval_transform = T.Compose([
        Invert(),
        center_crop,
        to_tensor_and_norm,
    ])

    return train_transform, eval_transform, update_transforms


def train_epoch(model, data_loader, fold_probs, optimizer, scheduler, epoch, config):
    writer = SummaryWriter(os.path.join(config.experiment_path, 'F{}'.format(config.fold), 'train'))
    metrics = {
        'loss': Mean(),
        'loss_hist': Concat(),
        'entropy': Mean(),
        'lr': Last(),
    }

    model.train()
    for images, targets, indices in tqdm(data_loader, desc='[F{}][epoch {}] train'.format(config.fold, epoch)):
        images, targets, indices = images.to(DEVICE), targets.to(DEVICE), indices.to(DEVICE)

        if epoch >= config.train.self_distillation.start_epoch:
            targets = weighted_sum(targets, fold_probs[indices], config.train.self_distillation.target_weight)
        if config.train.cutmix is not None:
            if np.random.uniform() > (epoch - 1) / (config.epochs - 1):
                images, targets = utils.cutmix(images, targets, config.train.cutmix)

        logits, etc = model(images)

        loss = compute_loss(input=logits, target=targets, config=config.train)

        metrics['loss'].update(loss.data.cpu().numpy())
        metrics['loss_hist'].update(loss.data.cpu().numpy())
        metrics['entropy'].update(compute_entropy(logits).data.cpu().numpy())
        metrics['lr'].update(np.squeeze(scheduler.get_lr()))

        loss.mean().backward()
        optimizer.step()
        optimizer.zero_grad()
        scheduler.step()

        # FIXME:
        if epoch >= config.train.self_distillation.start_epoch:
            probs = torch.cat([i.softmax(-1) for i in split_target(logits.detach())], -1)
            fold_probs[indices] = weighted_sum(fold_probs[indices], probs, config.train.self_distillation.pred_ewa)

    for k in metrics:
        if k.endswith('_hist'):
            writer.add_histogram(k, metrics[k].compute_and_reset(), global_step=epoch)
        else:
            writer.add_scalar(k, metrics[k].compute_and_reset(), global_step=epoch)
    writer.add_image('images', torchvision.utils.make_grid(
        images, nrow=compute_nrow(images), normalize=True), global_step=epoch)
    if 'stn' in etc:
        writer.add_image('stn', torchvision.utils.make_grid(
            etc['stn'], nrow=compute_nrow(etc['stn']), normalize=True), global_step=epoch)

    writer.flush()
    writer.close()


def eval_epoch(model, data_loader, epoch, config):
    writer = SummaryWriter(os.path.join(config.experiment_path, 'F{}'.format(config.fold), 'eval'))
    metrics = {
        'loss': Mean(),
        'loss_hist': Concat(),
        'entropy': Mean(),
        'hmar': HMAR(),
    }

    with torch.no_grad():
        model.eval()
        for images, targets, _ in tqdm(data_loader, desc='[F{}][epoch {}] eval'.format(config.fold, epoch)):
            images, targets = images.to(DEVICE), targets.to(DEVICE)

            logits, etc = model(images)

            loss = compute_loss(input=logits, target=targets, config=config.train)

            metrics['loss'].update(loss.data.cpu().numpy())
            metrics['loss_hist'].update(loss.data.cpu().numpy())
            metrics['entropy'].update(compute_entropy(logits).data.cpu().numpy())
            metrics['hmar'].update(
                input=decode_target(logits).data.cpu().numpy(),
                target=decode_target(targets).data.cpu().numpy())

    for k in metrics:
        if k.endswith('_hist'):
            writer.add_histogram(k, metrics[k].compute_and_reset(), global_step=epoch)
        else:
            writer.add_scalar(k, metrics[k].compute_and_reset(), global_step=epoch)
    writer.add_image('images', torchvision.utils.make_grid(
        images, nrow=compute_nrow(images), normalize=True), global_step=epoch)
    if 'stn' in etc:
        writer.add_image('stn', torchvision.utils.make_grid(
            etc['stn'], nrow=compute_nrow(etc['stn']), normalize=True), global_step=epoch)

    writer.flush()
    writer.close()


def lr_search(train_data, train_transform, config):
    train_dataset = LabeledDataset(train_data, transform=train_transform)
    train_data_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=config.train.batch_size,
        drop_last=True,
        shuffle=True,
        num_workers=config.workers,
        worker_init_fn=worker_init_fn)

    min_lr = 1e-5
    max_lr = 1.
    gamma = (max_lr / min_lr)**(1 / len(train_data_loader))

    lrs = []
    losses = []
    lim = None

    model = Model(config.model, num_classes=CLASS_META['num_classes'].sum()).to(DEVICE)
    optimizer = build_optimizer(model.parameters(), config.train.optimizer)
    for param_group in optimizer.param_groups:
        param_group['lr'] = min_lr
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma)

    # update_transforms(1.)
    model.train()
    for images, targets, _ in tqdm(train_data_loader, desc='lr_search'):
        images, targets = images.to(DEVICE), targets.to(DEVICE)

        if config.train.cutmix is not None:
            images, targets = utils.cutmix(images, targets, config.train.cutmix)

        logits, etc = model(images)

        loss = compute_loss(input=logits, target=targets, config=config.train)

        lrs.append(np.squeeze(scheduler.get_lr()))
        losses.append(loss.data.cpu().numpy().mean())

        if lim is None:
            lim = losses[0] * 1.1
        if lim < losses[-1]:
            break

        loss.mean().backward()
        optimizer.step()
        optimizer.zero_grad()
        scheduler.step()

    writer = SummaryWriter(os.path.join(config.experiment_path, 'lr_search'))

    with torch.no_grad():
        losses = np.clip(losses, 0, lim)
        minima_loss = losses[np.argmin(utils.smooth(losses))]
        minima_lr = lrs[np.argmin(utils.smooth(losses))]

        step = 0
        for loss, loss_sm in zip(losses, utils.smooth(losses)):
            writer.add_scalar('search_loss', loss, global_step=step)
            writer.add_scalar('search_loss_sm', loss_sm, global_step=step)
            step += config.train.batch_size

        fig = plt.figure()
        plt.plot(lrs, losses)
        plt.plot(lrs, utils.smooth(losses))
        plt.axvline(minima_lr)
        plt.xscale('log')
        plt.title('loss: {:.8f}, lr: {:.8f}'.format(minima_loss, minima_lr))
        writer.add_figure('lr_search', fig, global_step=0)
        print(minima_lr)

    writer.flush()
    writer.close()

    return minima_lr


def build_dataset_targets(dataset, config):
    data_loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=config.train.batch_size,
        num_workers=config.workers,
        worker_init_fn=worker_init_fn)
    probs = torch.zeros(len(dataset), CLASS_META['num_classes'].sum(), dtype=torch.float, device=DEVICE)

    for _, targets, indices in tqdm(data_loader, desc='build targets'):
        assert probs.dtype == targets.dtype
        probs[indices] = targets.to(DEVICE)

    return probs


@click.command()
@click.option('--config-path', type=click.Path(), required=True)
@click.option('--dataset-path', type=click.Path(), required=True)
@click.option('--experiment-path', type=click.Path(), required=True)
@click.option('--restore-path', type=click.Path())
@click.option('--fold', type=click.INT, required=True)
@click.option('--lr-search', is_flag=True)
@click.option('--workers', type=click.INT, default=os.cpu_count())
def main(**kwargs):
    # TODO: seed everything
    config = load_config(**kwargs)  # FIXME:
    del kwargs

    train_eval_data = load_labeled_data(
        os.path.join(config.dataset_path, 'train.csv'),
        glob.glob(os.path.join(config.dataset_path, 'train_image_data_*.parquet')),
        cache_path=os.path.join(config.dataset_path, 'train_images'))

    train_indices, eval_indices = indices_for_fold(train_eval_data, fold=config.fold, seed=config.seed)

    train_transform, eval_transform, update_transforms = build_transforms()

    if config.lr_search:
        update_transforms(1.)
        lr_search(train_eval_data, train_transform, config)
        return

    train_dataset = LabeledDataset(train_eval_data.iloc[train_indices], transform=train_transform)
    train_dataset = torch.utils.data.ConcatDataset([
        train_dataset,
        # FakeDataset(train_eval_data, len(train_dataset) // 4, transform=train_transform)
    ])
    eval_dataset = LabeledDataset(train_eval_data.iloc[eval_indices], transform=eval_transform)

    train_data_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=config.train.batch_size,
        drop_last=True,
        shuffle=True,
        num_workers=config.workers,
        worker_init_fn=worker_init_fn)
    eval_data_loader = torch.utils.data.DataLoader(
        eval_dataset,
        batch_size=config.eval.batch_size,
        num_workers=config.workers,
        worker_init_fn=worker_init_fn)

    model = Model(config.model, num_classes=CLASS_META['num_classes'].sum()).to(DEVICE)
    optimizer = build_optimizer(
        model.parameters(), config.train.optimizer)
    scheduler = build_scheduler(
        optimizer, config.train.scheduler, config.train.optimizer, config.epochs, len(train_data_loader))
    saver = Saver({
        'model': model,
        'optimizer': optimizer,
        'scheduler': scheduler,
    })
    if config.restore_path is not None:
        saver.load(config.restore_path, keys=['model'])

    update_transforms(0)
    fold_probs = build_dataset_targets(train_dataset, config)

    for epoch in range(1, config.epochs + 1):
        update_transforms((epoch - 1) / (config.epochs - 1))
        train_epoch(model, train_data_loader, fold_probs, optimizer, scheduler, epoch=epoch, config=config)
        eval_epoch(model, eval_data_loader, epoch=epoch, config=config)
        saver.save(
            os.path.join(
                config.experiment_path,
                'F{}'.format(config.fold),
                'checkpoint_{}.pth'.format(epoch)),
            epoch=epoch)


if __name__ == '__main__':
    main()
