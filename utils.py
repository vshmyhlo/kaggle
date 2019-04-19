import random
import matplotlib.pyplot as plt
from PIL import Image
import tempfile
import scipy
import os
import numpy as np
import torch


class Mean(object):
    def __init__(self):
        self.values = []

    def compute(self):
        return sum(self.values) / len(self.values)

    def update(self, value):
        self.values.extend(np.reshape(value, [-1]))

    def reset(self):
        self.values = []

    def compute_and_reset(self):
        value = self.compute()
        self.reset()

        return value


def smooth(x, ksize=None):
    if ksize is None:
        ksize = x.shape[0] // 4

        if ksize % 2 == 0:
            ksize += 1

    x = np.pad(x, (ksize // 2, ksize // 2), mode='reflect', reflect_type='odd')
    w = scipy.signal.windows.hann(ksize)
    w /= w.sum()
    x = scipy.signal.convolve(x, w, mode='valid')

    return x


def seed_everything(seed):
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    #     tf.set_random_seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = True


def plot_to_image():
    with tempfile.TemporaryFile() as f:
        plt.savefig(f)
        plt.close()
        image = Image.open(f)
        image = np.array(image)

        return image
