import numpy as np


class LRScheduler(object):
    def step(self):
        raise NotImplementedError

    def get_lr(self):
        raise NotImplementedError


class OneCycleScheduler(LRScheduler):
    def __init__(self, optimizer, lr, beta, max_steps, annealing, peak_pos=0.3):
        if annealing == 'linear':
            annealing = annealing_linear
        elif annealing == 'cosine':
            annealing = annealing_cosine
        else:
            raise AssertionError('invalid annealing {}'.format(annealing))

        self.optimizer = optimizer
        self.lr = lr
        self.beta = beta
        self.max_steps = max_steps
        self.annealing = annealing
        self.peak_pos = peak_pos
        self.epoch = -1

    def step(self):
        self.epoch += 1

        lr = self.get_lr()
        beta = self.get_beta()

        for param_group in self.optimizer.param_groups:
            param_group['lr'] = lr

            if 'betas' in param_group:
                param_group['betas'] = (beta, *param_group['betas'][1:])
            elif 'momentum' in param_group:
                param_group['momentum'] = beta
            else:
                raise AssertionError('no beta parameter')

    def get_lr(self):
        mid = round(self.max_steps * self.peak_pos)

        if self.epoch < mid:
            r = self.epoch / mid
            lr = self.annealing(self.lr[0], self.lr[1], r)
        else:
            r = (self.epoch - mid) / (self.max_steps - mid)
            lr = self.annealing(self.lr[1], self.lr[0] / 1e4, r)

        return lr

    def get_beta(self):
        mid = round(self.max_steps * self.peak_pos)

        if self.epoch < mid:
            r = self.epoch / mid
            beta = self.annealing(self.beta[0], self.beta[1], r)
        else:
            r = (self.epoch - mid) / (self.max_steps - mid)
            beta = self.annealing(self.beta[1], self.beta[0], r)

        return beta


def annealing_linear(start, end, r):
    return start + r * (end - start)


def annealing_cosine(start, end, r):
    cos_out = np.cos(np.pi * r) + 1

    return end + (start - end) / 2 * cos_out
