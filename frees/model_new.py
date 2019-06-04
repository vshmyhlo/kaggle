import torch
import numpy as np
from gammatone.fftweight import fft_weights
import efficientnet_pytorch
from efficientnet_pytorch import EfficientNet
import torch.distributions
import torchvision
import librosa
import torch.nn as nn
import torch.nn.functional as F


class ReLU(nn.RReLU):
    pass


class Model(nn.Module):
    def __init__(self, model, num_classes):
        super().__init__()

        self.spectrogram = Spectrogram(model.sample_rate)

        if model.type == 'max':
            self.model = MaxPoolModel(num_classes, dropout=model.dropout)
        else:
            raise AssertionError('invalid model {}'.format(model.type))

    def forward(self, input):
        images = self.spectrogram(input)
        logits, weights = self.model(images)

        return logits, images, weights


# class Model(nn.Module):
#     def __init__(self, model, num_classes):
#         super().__init__()
#
#         self.l1 = nn.Sequential(
#             Conv1dNormRelu(1, 16, 64, stride=2),
#             nn.MaxPool1d(8, 8))
#         self.l2 = nn.Sequential(
#             Conv1dNormRelu(16, 32, 32, stride=2),
#             nn.MaxPool1d(8, 8))
#         self.l3 = nn.Sequential(
#             Conv1dNormRelu(32, 64, 16, stride=2),
#             Conv1dNormRelu(64, 128, 8, stride=2),
#             Conv1dNormRelu(128, 256, 4, stride=2),
#             nn.AdaptiveMaxPool1d(1))
#         self.output = nn.Linear(256, num_classes)
#
#         for m in self.modules():
#             if isinstance(m, nn.Conv1d):
#                 nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
#             elif isinstance(m, nn.BatchNorm1d):
#                 nn.init.constant_(m.weight, 1)
#                 nn.init.constant_(m.bias, 0)
#
#     def forward(self, input):
#         input = input.unsqueeze(1)
#
#         input = self.l1(input)
#         input = self.l2(input)
#         input = self.l3(input)
#
#         input = input.squeeze(2)
#         input = self.output(input)
#
#         return input, torch.zeros(input.size(0), 1, 1, 1), torch.zeros(input.size(0), 1, 1, 1)


class ConvNormRelu1d(nn.Sequential):
    def __init__(self, in_channels, out_channels, kernel_size, stride=1, padding=0):
        super().__init__(
            nn.Conv1d(in_channels, out_channels, kernel_size, stride=stride, padding=padding, bias=False),
            nn.BatchNorm1d(out_channels),
            ReLU(inplace=True))


class ConvNorm2d(nn.Sequential):
    def __init__(self, in_channels, out_channels, kernel_size, stride=1, padding=0):
        super().__init__(
            nn.Conv2d(in_channels, out_channels, kernel_size, stride=stride, padding=padding, bias=False),
            nn.BatchNorm2d(out_channels))


class ConvNormRelu2d(nn.Sequential):
    def __init__(self, in_channels, out_channels, kernel_size, stride=1, padding=0):
        super().__init__(
            nn.Conv2d(in_channels, out_channels, kernel_size, stride=stride, padding=padding, bias=False),
            nn.BatchNorm2d(out_channels),
            ReLU(inplace=True))


class SplitConv(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()

        self.a = nn.Sequential(
            ConvNormRelu2d(in_channels, out_channels // 2, (7, 1), stride=(2, 1), padding=(3, 0)),
            ConvNormRelu2d(out_channels // 2, out_channels // 2, (1, 7), stride=(1, 2), padding=(0, 3)))
        self.b = nn.Sequential(
            ConvNormRelu2d(in_channels, out_channels // 2, (1, 7), stride=(1, 2), padding=(0, 3)),
            ConvNormRelu2d(out_channels // 2, out_channels // 2, (7, 1), stride=(2, 1), padding=(3, 0)))

    def forward(self, input):
        a = self.a(input)
        b = self.b(input)
        input = torch.cat([a, b], 1)

        return input


# class CustomBlock(nn.Sequential):
#     def __init__(self, in_features, out_features):
#         super().__init__(
#             ConvNormRelu2d(in_features, out_features, 3, 1, 1),
#             ConvNormRelu2d(out_features, out_features, 3, 1, 1),
#             nn.MaxPool2d(2, 2))

# class CustomBlock(nn.Sequential):
#     def __init__(self, in_channels, out_channels, kernel_size=5):
#         super().__init__()
#
#         padding = kernel_size // 2
#
#         self.a = nn.Sequential(
#             ConvNormRelu2d(in_channels, out_channels // 2, (kernel_size, 1), stride=(2, 1), padding=(padding, 0)),
#             ConvNormRelu2d(out_channels // 2, out_channels // 2, (1, kernel_size), stride=(1, 2), padding=(0, padding)))
#         self.b = nn.Sequential(
#             ConvNormRelu2d(in_channels, out_channels // 2, (1, kernel_size), stride=(1, 2), padding=(0, padding)),
#             ConvNormRelu2d(out_channels // 2, out_channels // 2, (kernel_size, 1), stride=(2, 1), padding=(padding, 0)))
#
#     def forward(self, input):
#         a = self.a(input)
#         b = self.b(input)
#         input = torch.cat([a, b], 1)
#
#         return input


# class CustomBlock(nn.Sequential):
#     def __init__(self, in_channels, out_channels, kernel_size=5, stride=1):
#         super().__init__()
#
#         padding = kernel_size // 2
#
#         self.a = nn.Sequential(
#             ConvNormRelu2d(
#                 in_channels, out_channels // 2, (kernel_size, 1), stride=(stride, 1), padding=(padding, 0)),
#             ConvNormRelu2d(
#                 out_channels // 2, out_channels // 2, (1, kernel_size), stride=(1, stride), padding=(0, padding)))
#         self.b = nn.Sequential(
#             ConvNormRelu2d(
#                 in_channels, out_channels // 2, (1, kernel_size), stride=(1, stride), padding=(0, padding)),
#             ConvNormRelu2d(
#                 out_channels // 2, out_channels // 2, (kernel_size, 1), stride=(stride, 1), padding=(padding, 0)))
#
#         self.identity = None
#
#     def forward(self, input):
#         a = self.a(input)
#         b = self.b(input)
#         input = torch.cat([a, b], 1)
#
#         return input

# class CustomBlock(nn.Sequential):
#     def __init__(self, in_channels, out_channels, kernel_size=5, stride=1):
#         super().__init__()
#
#         padding = kernel_size // 2
#
#         self.a = nn.Sequential(
#             ConvNormRelu2d(in_channels, out_channels, (kernel_size, 1), stride=(stride, 1), padding=(padding, 0)),
#             ConvNormRelu2d(out_channels, out_channels, (1, kernel_size), stride=(1, stride), padding=(0, padding)))
#         self.b = nn.Sequential(
#             ConvNormRelu2d(in_channels, out_channels, (1, kernel_size), stride=(1, stride), padding=(0, padding)),
#             ConvNormRelu2d(out_channels, out_channels, (kernel_size, 1), stride=(stride, 1), padding=(padding, 0)))
#         self.w = nn.Sequential(
#             ConvNorm2d(in_channels, out_channels, 3, stride=stride, padding=1),
#             nn.Sigmoid())
#
#     def forward(self, input):
#         a = self.a(input)
#         b = self.b(input)
#         w = self.w(input)
#         input = w * a + (1 - w) * b
#
#         return input


class CustomBlock(nn.Sequential):
    def __init__(self, in_channels, out_channels, kernel_size=5, stride=1):
        super().__init__()

        padding = kernel_size // 2

        self.a = ConvNormRelu2d(in_channels, out_channels, (kernel_size, 1), stride=stride, padding=(padding, 0))
        self.b = ConvNormRelu2d(in_channels, out_channels, (1, kernel_size), stride=stride, padding=(0, padding))
        self.w = nn.Sequential(
            ConvNorm2d(in_channels, out_channels, 1),
            ReLU(inplace=True),
            ConvNorm2d(in_channels, out_channels, 1),
            nn.Sigmoid())

    def forward(self, input):
        a = self.a(input)
        b = self.b(input)

        print(input.size())
        kernel_size = input.size(2)
        padding = 0
        if kernel_size % 2 == 0:
            kernel_size += 1
            padding = 1
        input = F.max_pool2d(input, kernel_size, 1, padding=(padding, kernel_size // 2))
        print(input.size())

        w = self.w(input)
        input = w * a + (1 - w) * b

        return input


class CustomModel(nn.Module):
    def __init__(self, num_classes, dropout):
        super().__init__()

        in_channels = 1
        channels = 32

        self.blocks = nn.Sequential(
            CustomBlock(in_channels, channels * 1, 5, 2),
            CustomBlock(channels * 1, channels * 1, 3, 1),
            CustomBlock(channels * 1, channels * 2, 5, 2),
            CustomBlock(channels * 2, channels * 2, 3, 1),
            CustomBlock(channels * 2, channels * 4, 5, 2),
            CustomBlock(channels * 4, channels * 4, 3, 1),
            CustomBlock(channels * 4, channels * 8, 5, 2),
            CustomBlock(channels * 8, channels * 8, 3, 1),
            CustomBlock(channels * 8, channels * 16, 5, 2),
            CustomBlock(channels * 16, channels * 16, 3, 1))
        self.pool = nn.AdaptiveMaxPool2d(1)
        self.output = nn.Sequential(
            nn.Dropout2d(dropout),
            nn.Linear(channels * 16, num_classes))

    def forward(self, input):
        input = self.blocks(input)
        input = self.pool(input)
        input = input.view(input.size(0), input.size(1))
        input = self.output(input)

        return input


class MaxPoolModel(nn.Module):
    def __init__(self, num_classes, dropout):
        super().__init__()

        # self.model = pretrainedmodels.resnet18(pretrained=None)
        # self.model.conv1 = nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3, bias=False)
        # self.model.avgpool = nn.AdaptiveMaxPool2d(1)
        # self.model.last_linear = nn.Sequential(
        #     nn.Dropout(dropout),
        #     nn.Linear(512, num_classes))

        # self.model = ResNet(BasicBlock, [2, 2, 2, 2], num_classes=num_classes)
        # # self.model.conv1 = nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3, bias=False)
        # self.model.layer0 = SplitConv(1, 64)
        # self.model.avgpool = nn.AdaptiveMaxPool2d(1)
        # self.model.fc = nn.Sequential(
        #     nn.Dropout(dropout),
        #     self.model.fc)

        self.model = CustomModel(num_classes, dropout)

        for m in self.model.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

    def forward(self, input):
        b, _, h, w = input.size()

        input = self.model(input)
        weights = torch.zeros(b, 1, h, w)

        return input, weights


class ResNet(nn.Module):
    def __init__(self, block, layers, num_classes=1000, zero_init_residual=False):
        super().__init__()
        self.inplanes = 64

        self.layer0 = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3, bias=False),
            nn.BatchNorm2d(64),
            ReLU(inplace=True))
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
        self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
        self.layer4 = self._make_layer(block, 512, layers[3], stride=2)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.fc = nn.Linear(512 * block.expansion, num_classes)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        # Zero-initialize the last BN in each residual branch,
        # so that the residual branch starts with zeros, and each residual block behaves like an identity.
        # This improves the model by 0.2~0.3% according to https://arxiv.org/abs/1706.02677
        if zero_init_residual:
            for m in self.modules():
                if isinstance(m, torchvision.models.resnet.Bottleneck):
                    nn.init.constant_(m.bn3.weight, 0)
                elif isinstance(m, BasicBlock):
                    nn.init.constant_(m.bn2.weight, 0)

    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                torchvision.models.resnet.conv1x1(self.inplanes, planes * block.expansion, stride),
                nn.BatchNorm2d(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.layer0(x)
        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)

        return x


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super().__init__()
        self.conv1 = torchvision.models.resnet.conv3x3(inplanes, planes, stride)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = ReLU(inplace=True)
        self.conv2 = torchvision.models.resnet.conv3x3(planes, planes)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out


class Spectrogram(nn.Module):
    def __init__(self, rate):
        super().__init__()

        self.n_fft = round(0.025 * rate)
        self.hop_length = round(0.01 * rate)

        filters = librosa.filters.mel(rate, self.n_fft)
        # filters, _ = fft_weights(self.n_fft, rate, 128, width=1, fmin=0, fmax=rate / 2, maxlen=self.n_fft / 2 + 1)

        filters = filters.reshape((*filters.shape, 1))
        filters = torch.tensor(filters).float()

        self.mel = nn.Conv1d(512, 128, 1, bias=False)
        self.mel.weight.data = filters
        self.mel.weight.requires_grad = False

        self.norm = nn.BatchNorm2d(1)

    def forward(self, input):
        input = torch.stft(input, n_fft=self.n_fft, hop_length=self.hop_length)
        input = torch.norm(input, 2, -1)**2  # TODO:
        input = self.mel(input)

        amin = torch.tensor(1e-10).to(input.device)
        input = 10.0 * torch.log10(torch.max(amin, input))

        input = input.unsqueeze(1)
        input = self.norm(input)

        return input