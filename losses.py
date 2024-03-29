import torch
import torch.nn.functional as F

from lovasz_losses import lovasz_softmax as _lovasz_softmax


# TODO: add reduction argument
# TODO: reduce same way everything


# def focal_loss(input, target, axis=-1, gamma=2.):
#     target = target.float()
#     max_val = (-input).clamp(min=0)
#     loss = input - input * target + max_val + ((-max_val).exp() + (-input - max_val).exp()).log()
#
#     invprobs = F.logsigmoid(-input * (target * 2.0 - 1.0))
#     loss = (invprobs * gamma).exp() * loss
#
#     loss = loss.sum(axis)
#
#     return loss


def f2_loss(input, target, beta=1., eps=1e-7):
    input = input.sigmoid()

    tp = (target * input).sum(-1)
    # tn = ((1 - target) * (1 - input)).sum(-1)
    fp = ((1 - target) * input).sum(-1)
    fn = (target * (1 - input)).sum(-1)

    p = tp / (tp + fp + eps)
    r = tp / (tp + fn + eps)

    beta_sq = beta**2
    f2 = (1 + beta_sq) * p * r / (beta_sq * p + r + eps)
    loss = -(f2 + eps).log()

    return loss


def hinge_loss(input, target, delta=1.):
    positive_indices = (target > 0.5).float()
    negative_indices = (target <= 0.5).float()

    loss = 0.
    for i in range(input.size(0)):
        pos = positive_indices[i].nonzero()
        neg = negative_indices[i].nonzero()
        pos_examples = input[i, pos]
        neg_examples = torch.transpose(input[i, neg], 0, 1)
        loss += torch.sum(torch.max(torch.tensor(0.), delta + neg_examples - pos_examples))

    return loss


def bce_loss(input, target):
    loss = F.binary_cross_entropy_with_logits(input=input, target=target, reduction='sum')
    loss /= input.size(0)

    return loss


def lsep_loss(input, target):
    positive_indices = (target > 0.5).float()
    negative_indices = (target <= 0.5).float()

    loss = 0.
    for i in range(input.size(0)):
        pos = positive_indices[i].nonzero()
        neg = negative_indices[i].nonzero()
        pos_examples = input[i, pos]
        neg_examples = torch.transpose(input[i, neg], 0, 1)
        loss += torch.log(1 + torch.sum(torch.exp(neg_examples - pos_examples)))

    loss /= input.size(0)

    return loss


def dice_loss(input, target, smooth=1., axis=None):
    intersection = (input * target).sum(axis)
    union = input.sum(axis) + target.sum(axis)
    dice = (2. * intersection + smooth) / (union + smooth)

    loss = 1 - dice

    return loss


def wtf_loss(input, target, axis=None):
    hit = (input * target).sum(axis) + \
          ((1 - input) * (1 - target)).sum(axis)
    miss = (input * (1 - target)).sum(axis) + \
           ((1 - input) * target).sum(axis)

    loss = miss / hit

    return loss


def iou_loss(input, target, axis=None, eps=1e-7):
    intersection = (input * target).sum(axis)
    union = input.sum(axis) + target.sum(axis) - intersection
    iou = intersection / (union + eps)

    loss = 1 - iou

    return loss


def sigmoid_cross_entropy(input, target):
    loss = F.binary_cross_entropy_with_logits(input=input, target=target, reduction='none')

    return loss


def softmax_cross_entropy(input, target, axis=1, keepdim=False):
    log_prob = input.log_softmax(axis)
    loss = -(target * log_prob).sum(axis, keepdim=keepdim)

    return loss


def sigmoid_focal_loss(input, target, gamma=2.):
    prob = input.sigmoid()
    prob_true = prob * target + (1 - prob) * (1 - target)
    weight = (1 - prob_true)**gamma

    loss = sigmoid_cross_entropy(input=input, target=target)
    loss = weight * loss

    return loss


def softmax_focal_loss(input, target, gamma=2., axis=1, keepdim=False):
    prob = input.softmax(axis)
    weight = (1 - prob)**gamma

    log_prob = input.log_softmax(axis)
    loss = -(weight * target * log_prob).sum(axis, keepdim=keepdim)

    return loss


def fbeta_score(input, target, beta=1., eps=1e-7):
    input = input.sigmoid()

    tp = (target * input).sum(-1)
    # tn = ((1 - target) * (1 - input)).sum(-1)
    fp = ((1 - target) * input).sum(-1)
    fn = (target * (1 - input)).sum(-1)

    p = tp / (tp + fp + eps)
    r = tp / (tp + fn + eps)

    beta_sq = beta**2
    fbeta = (1 + beta_sq) * p * r / (beta_sq * p + r + eps)

    return fbeta


def softmax_lovasz_loss(input, target):
    input = input.softmax(1)
    target = target.argmax(1)
    loss = _lovasz_softmax(probas=input, labels=target)

    return loss


# TODO: refactor sum
def tversky_loss(input, target, alpha=0.5, beta=0.5, eps=1e-7, axis=None):
    intersection = torch.sum(input * target, axis)
    fp = torch.sum(input * (torch.tensor(1.) - target), axis)
    fn = torch.sum((torch.tensor(1.) - input) * target, axis)

    numerator = intersection
    denominator = intersection + alpha * fp + beta * fn
    tversky = numerator / (denominator + eps)
    loss = 1 - tversky

    return loss
