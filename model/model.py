"""Model based on the paper.

"High Resolution Face Completion with Multiple Controllable Attributes
via Fully End-to-End Progressive Generative Adversarial Networks."
<https://arxiv.org/abs/1801.07632.pdf>
"""

from math import ceil

import numpy as np
import torch
from torch.nn.init import kaiming_normal_
import torch.nn as nn
from torch.nn import functional as F
from util.spectral_norm import spectral_norm

"""
TODO
1. Add Conv Layer width, height calculation formula.
2. fmap_* parameter name change / explanation add.
"""


def downsample(x, factor):
    """Downsample tensor.

    Args:
        factor (int): factor to downsample.

    Return: downsampled tensor.
    """
    return F.avg_pool2d(x, factor)


def upsample(x, factor):
    """Upsample tensor.

    Args:
        factor (int): factor to upsample.

    Return: upsampled tensor.
    """
    return F.upsample(x, scale_factor=factor)


class Dense(nn.Module):
    """Simple Fully Connected Network class."""

    def __init__(self, in_channels, num_classes=1,
                 nonlinearity=None, spectralnorm=True):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            num_classes (int): The number of output channels, Default is 1.
            nonlinearity: Non-linearity functions of torch. Default is None.
        """
        super(Dense, self).__init__()
        self.linear = nn.Linear(in_channels, num_classes)
        if spectralnorm:
            self.linear = spectral_norm(self.linear)
        self.nonlinearity = nonlinearity
        if num_classes > 1:
            self.softmax = nn.Softmax(1)
        else:
            self.softmax = nn.Sigmoid()

    def forward(self, x):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input image batch.

        Returns:
            x (tensor): [batch_size, num_classes],
                        Predicted prob of each class.

        """
        x = self.linear(x)
        if self.nonlinearity is not None:
            x = self.nonlinearity(x)
        x = self.softmax(x)
        return x


class PGConv2d(nn.Module):
    """Simple Convolutional Network class for Progressive GAN."""

    def __init__(self, in_channels, out_channels, nonlinearity,
                 kernel_size=3, stride=1, pad=1, instancenorm=True,
                 spectralnorm=False):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            out_channels (int): The number of output channels.
            nonlinearity: nonlinearity function
            kernel_size (int): Filter kernel size, Default is 3.
            stride (int): Filter stride size, Default is 1.
            pad: pad size, Default is 1.
            instancenorm (bool): Whether use instance normalization or not,
                                 Default is True.
            spectralnorm (bool): Whether use spectral normalization or not,
                                 Default is False.
        """
        super(PGConv2d, self).__init__()

        self.conv = nn.Conv2d(in_channels, out_channels,
                              kernel_size, stride, pad)
        if spectralnorm:
            self.conv = spectral_norm(self.conv)
        kaiming_normal_(self.conv.weight)
        self.instancenorm = instancenorm
        self.nonlinearity = nonlinearity
        self.out_channels = out_channels

    def forward(self, x):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input tensor.

        Returns:
            x (tensor): [batch_size, out_channels, height', width'],
                        output tensor.

        """
        x = self.conv(x)
        if self.nonlinearity is not None:
            x = self.nonlinearity(x)
        if self.instancenorm:
            x = nn.InstanceNorm2d(self.out_channels)(x)
        return x


class G_EncLastBlock(nn.Module):
    """Generator encoder's last block class."""

    def __init__(self, in_channels, out_channels, num_channels,
                 nonlinearity, instancenorm=True):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            out_channels (int): The number of output channels.
            num_channels (int): The number of input image channels.
            nonlinearity: nonlinearity function
            instancenorm (bool): Whether use instance normalization or not,
                                 Default is True.
        """
        super(G_EncLastBlock, self).__init__()
        self.fromRGB = PGConv2d(num_channels, in_channels, nonlinearity,
                                kernel_size=1, pad=0)
        self.conv1 = PGConv2d(in_channels, out_channels,
                              nonlinearity, kernel_size=3, stride=1,
                              instancenorm=False)
        # downsample
        self.conv2 = PGConv2d(out_channels, out_channels,
                              nonlinearity, kernel_size=4, stride=1,
                              pad=0, instancenorm=False)

    def forward(self, x, first=False):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input tensor.
            first (bool): Whether first layer of Generator or not.

        Returns:
            x (tensor): [batch_size, out_channels, height'', width''],
                        output tensor.
            h (tensor): [batch_size, out_channels, height', width'],
                        intermediate output tensor.

        """
        if first:
            x = self.fromRGB(x)
        h = self.conv1(x)
        x = self.conv2(h)
        return x, h


class G_EncBlock(nn.Module):
    """Generator encoder's normal block class."""

    def __init__(self, in_channels, out_channels, num_channels,
                 nonlinearity, instancenorm=True):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            out_channels (int): The number of output channels.
            num_channels (int): The number of input image channels.
            nonlinearity: nonlinearity function
            instancenorm (bool): Whether use instance normalization or not,
                                 Default is True.
        """
        super(G_EncBlock, self).__init__()
        self.fromRGB = PGConv2d(num_channels, in_channels, nonlinearity,
                                kernel_size=1, pad=0, instancenorm=False)
        self.conv1 = PGConv2d(in_channels, in_channels,
                              nonlinearity, instancenorm=instancenorm)
        self.conv2 = PGConv2d(in_channels, out_channels,
                              nonlinearity, instancenorm=instancenorm)

    def forward(self, x, first=False):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input tensor.
            first (bool): Whether first layer of Generator or not.

        Returns:
            x (tensor): [batch_size, out_channels, height', width'],
                        output tensor.

        """
        if first:
            x = self.fromRGB(x)
        x = self.conv1(x)
        x = self.conv2(x)
        return x


class G_DecFirstBlock(nn.Module):
    """Generator decoder's first block class."""

    def __init__(self, in_channels, out_channels, num_channels,
                 nonlinearity, instancenorm=True):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            out_channels (int): The number of output channels.
            num_channels (int): The number of input image channels.
            nonlinearity: nonlinearity function
            instancenorm (bool): Whether use instance normalization or not,
                                 Default is True.
        """
        super(G_DecFirstBlock, self).__init__()
        self.conv1 = PGConv2d(in_channels, out_channels,
                              nonlinearity, kernel_size=4, stride=1,
                              pad=3, instancenorm=instancenorm)
        self.conv2 = PGConv2d(2*out_channels, out_channels,
                              nonlinearity, kernel_size=1, stride=1,
                              pad=0, instancenorm=instancenorm)
        self.toRGB = PGConv2d(out_channels, num_channels, nonlinearity=None,
                              kernel_size=1, pad=0, instancenorm=False)

    def forward(self, x, h, last=False):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input tensor.
            h (tensor): [batch_size, in_channels, height, width],
                        tensor skip connected from Generator.
            last (bool): Whether Decoder's last layer or not.

        Returns:
            x (tensor): if last: [batch_size, num_channels, height', width'],
                        else: [batch_size, out_channels, height', width'],
                        output tensor.

        """
        x = self.conv1(x)
        x = torch.cat([x, h], dim=1)
        x = self.conv2(x)
        if last:
            x = self.toRGB(x)
        return x


class G_DecBlock(nn.Module):
    """Generator's decoder block class."""

    def __init__(self, in_channels, out_channels, num_channels,
                 nonlinearity, instancenorm=True):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            out_channels (int): The number of output channels.
            num_channels (int): The number of input image channels.
            nonlinearity: nonlinearity function
            instancenorm (bool): Whether use instance normalization or not,
                                 Default is True.
        """
        super(G_DecBlock, self).__init__()
        self.conv1 = PGConv2d(2*in_channels, out_channels,
                              nonlinearity, instancenorm=instancenorm)
        self.conv2 = PGConv2d(out_channels, out_channels,
                              nonlinearity, kernel_size=1, stride=1,
                              pad=0, instancenorm=instancenorm)
        self.toRGB = PGConv2d(out_channels, num_channels, nonlinearity=None,
                              kernel_size=1, pad=0, instancenorm=False)

    def forward(self, x, h, last=False):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input tensor.
            h (tensor): [batch_size, in_channels, height, width],
                        tensor skip connected from Generator.
            last (bool): Whether Decoder's last layer or not.

        Returns:
            x (tensor): if last: [batch_size, num_channels, height', width'],
                        else: [batch_size, out_channels, height', width'],
                        output tensor.

        """
        x = torch.cat([x, h], dim=1)
        x = self.conv1(x)
        x = self.conv2(x)
        if last:
            x = self.toRGB(x)
        return x


class Generator(nn.Module):
    """Generator model class.

    Based on "High Resolution Face Completion with Multiple Controllable
    Attributes via Fully End-to-End Progressive Generative Adversarial
    Networks"
    <https://arxiv.org/abs/1801.07632.pdf>
    """

    def __init__(self,
                 dataset_shape,
                 fmap_base=1024,
                 fmap_min=4,
                 fmap_max=256,
                 latent_size=256,
                 use_mask=True,
                 leaky_relu=True,
                 instancenorm=True):
        """constructor.

        Args:
            dataset_shape (list): Shape of input dataset.
            fmap_base (int): Decide the number of network parameter.
            fmap_min (int): Decide the number of network parameter.
            fmap_max (int): Decide the number of network parameter.
            latent_size (int): Latent vector dimension size.
            use_mask (bool): Whether use mask or not.
            leaky_relu (bool): Use leaky_relu(True) or ReLU(False)
            instancenorm (bool): Whether use Instancenorm or not.

        """
        super(Generator, self).__init__()
        resolution = dataset_shape[-1]
        num_channels = dataset_shape[1]
        self.use_mask = use_mask

        if self.use_mask:
            adjusted_channels = num_channels+1
        else:
            adjusted_channels = num_channels

        R = int(np.log2(resolution))
        assert resolution == 2 ** R and resolution >= 4

        def nf(stage):
            return min(int(fmap_base / (2.0 ** stage)), fmap_max)

        if latent_size is None:
            latent_size = nf(0)

        nonlinearity = nn.LeakyReLU(0.2) if leaky_relu else nn.ReLU()

        # encoder blocks
        self.encblocks = []
        for i in reversed(range(1, R-1)):
            self.encblocks.append(G_EncBlock(nf(i), nf(i-1),
                                             adjusted_channels,
                                             nonlinearity,
                                             instancenorm=instancenorm))
        self.encblocks.append(G_EncLastBlock(latent_size, latent_size,
                                             adjusted_channels, nonlinearity))
        self.encblocks = nn.ModuleList(self.encblocks)

        # decoder blocks
        self.decblocks = []
        self.decblocks.append(G_DecFirstBlock(latent_size, latent_size,
                                              num_channels, nonlinearity))
        for i in range(1, R-1):
            self.decblocks.append(G_DecBlock(nf(i-1), nf(i),
                                             num_channels,
                                             nonlinearity,
                                             instancenorm=instancenorm))
        self.decblocks = nn.ModuleList(self.decblocks)

    def forward(self, x, mask=None, cur_level=None):
        """forward.

        Args:
            x (tensor): [batch_size, num_channels, height, width],
                        Input image batch.
            mask (tensor): [batch_size, num_channels, height, width], Defaults
                           to None.
            cur_level (int): The level of current training status.

        Returns:
            h (tensor): [batch_size, num_channels, height, width],
                        Generated image batch.

        """
        if cur_level is None:
            cur_level = len(self.encblocks)

        max_level = ceil(cur_level)
        alpha = int(cur_level+1) - cur_level
        if self.use_mask:
            assert mask is not None, "mask is None put some value on it"
            x = torch.cat([x, mask], dim=1)

        # encoder
        hs = []
        if max_level > 1:
            h = self.encblocks[-max_level](x, True)
            hs.append(h)
            h = downsample(h, 2)

            if alpha < 1.0:
                x_down = downsample(x, 2)
                skip_connect = self.encblocks[-max_level+1].fromRGB(x_down)
                h = h*alpha + (1-alpha)*skip_connect

            for level in range(max_level-1, 0, -1):
                if level == 1:
                    h, h_prime = self.encblocks[-level](h)
                    hs.append(h_prime)
                else:
                    h = self.encblocks[-level](h)
                    hs.append(h)
                    h = downsample(h, 2)
        else:
            h, h_prime = self.encblocks[-max_level](x, True)
            hs.append(h_prime)

        # decoder
        if max_level > 1:
            for level in range(0, max_level-1, 1):
                if level == 0:
                    h = self.decblocks[level](h, hs[-level-1])
                else:
                    h = upsample(h, 2)
                    h = self.decblocks[level](h, hs[-level-1])

            x = h  # remember for to_RGB
            h = upsample(h, 2)  # last layer
            h = self.decblocks[max_level-1](h, hs[-level-2], True)

            if alpha < 1.0:
                x_upsample = upsample(x, 2)
                skip_connect = self.decblocks[max_level-2].toRGB(x_upsample)
                h = h*alpha + skip_connect*(1-alpha)
        else:
            h = self.decblocks[0](h, hs[0], True)
        return h


class D_EncBlock(nn.Module):
    """Discriminator encoder block class."""

    def __init__(self, in_channels, out_channels, num_channels, nonlinearity,
                 instancenorm=True, spectralnorm=True):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            out_channels (int): The number of output channels.
            num_channels (int): The number of input image channels.
            nonlinearity: nonlinearity function
            instancenorm (bool): Whether use instance normalization or not,
                                 Default is True.
            spectralnorm (bool): Whether use spectral normalization or not,
                                 Default is True.
        """
        super(D_EncBlock, self).__init__()
        self.fromRGB = PGConv2d(num_channels, in_channels, nonlinearity,
                                kernel_size=1, pad=0, instancenorm=False,
                                spectralnorm=spectralnorm)
        self.conv1 = PGConv2d(in_channels, in_channels,
                              nonlinearity, instancenorm=instancenorm,
                              spectralnorm=spectralnorm)
        self.conv2 = PGConv2d(in_channels, out_channels,
                              nonlinearity, instancenorm=instancenorm,
                              spectralnorm=spectralnorm)

    def forward(self, x, first=False):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input tensor.
            first (bool): Whether first layer of Generator or not.

        Returns:
            x (tensor): [batch_size, out_channels, height', width'],
                        output tensor.

        """
        if first:
            x = self.fromRGB(x)
        x = self.conv1(x)
        x = self.conv2(x)
        return x


class D_EncLastBlock(nn.Module):
    """Discriminator encoder's last block class."""

    def __init__(self, in_channels, out_channels, num_channels,
                 nonlinearity, instancenorm=True, spectralnorm=True):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            out_channels (int): The number of output channels.
            num_channels (int): The number of input image channels.
            nonlinearity: nonlinearity function
            instancenorm (bool): Whether use instance normalization or not,
                                 Default is True.
            spectralnorm (bool): Whether use spectral normalization or not,
                                 Default is True.
        """
        super(D_EncLastBlock, self).__init__()
        self.fromRGB = PGConv2d(num_channels, in_channels,
                                nonlinearity, kernel_size=1, pad=0,
                                spectralnorm=spectralnorm)
        self.conv1 = PGConv2d(in_channels, out_channels,
                              nonlinearity, instancenorm=False,
                              spectralnorm=spectralnorm)
        self.conv2 = PGConv2d(in_channels, in_channels,
                              nonlinearity, kernel_size=4, stride=1,
                              pad=0, instancenorm=False,
                              spectralnorm=spectralnorm)

    def forward(self, x, first=False):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input tensor.
            first (bool): Whether first layer of Generator or not.

        Returns:
            x (tensor): [batch_size, out_channels, height', width'],
                        output tensor.

        """
        if first:
            x = self.fromRGB(x)
        h = self.conv1(x)
        x = self.conv2(h)
        return x, h


class D_DecFirstBlock(nn.Module):
    """Discriminator decoder's first block class."""

    def __init__(self, in_channels, out_channels, num_channels, nonlinearity,
                 instancenorm=True, spectralnorm=True):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            out_channels (int): The number of output channels.
            num_channels (int): The number of input image channels.
            nonlinearity: nonlinearity function
            instancenorm (bool): Whether use instance normalization or not,
                                 Default is True.
            spectralnorm (bool): Whether use spectral normalization or not,
                                 Default is True.
        """
        super(D_DecFirstBlock, self).__init__()
        self.conv1 = PGConv2d(in_channels, out_channels,
                              nonlinearity, kernel_size=4, stride=1,
                              pad=3, instancenorm=instancenorm,
                              spectralnorm=True)
        self.conv2 = PGConv2d(2*out_channels, out_channels,
                              nonlinearity, kernel_size=1, stride=1,
                              pad=0, instancenorm=instancenorm,
                              spectralnorm=True)
        self.toRGB = PGConv2d(out_channels, num_channels, nonlinearity=None,
                              kernel_size=1, pad=0, instancenorm=False,
                              spectralnorm=True)

    def forward(self, x, h, last=False):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input tensor.
            h (tensor): [batch_size, in_channels, height, width],
                        tensor skip connected from Generator.
            last (bool): Whether Decoder's last layer or not.

        Returns:
            x (tensor): if last: [batch_size, num_channels, height', width'],
                        else: [batch_size, out_channels, height', width'],
                        output tensor.

        """
        x = self.conv1(x)
        x = torch.cat([x, h], dim=1)
        x = self.conv2(x)
        if last:
            x = self.toRGB(x)
        return x


class D_DecBlock(nn.Module):
    """Discriminator's decoder block class."""

    def __init__(self, in_channels, out_channels, num_channels, nonlinearity,
                 instancenorm=True, spectralnorm=True):
        """constructor.

        Args:
            in_channels (int): The number of input channels.
            out_channels (int): The number of output channels.
            num_channels (int): The number of input image channels.
            nonlinearity: nonlinearity function
            instancenorm (bool): Whether use instance normalization or not,
                                 Default is True.
            spectralnorm (bool): Whether use spectral normalization or not,
                                 Default is True.
        """
        super(D_DecBlock, self).__init__()
        self.conv1 = PGConv2d(2*in_channels, out_channels,
                              nonlinearity, instancenorm=instancenorm)
        self.conv2 = PGConv2d(out_channels, out_channels,
                              nonlinearity, kernel_size=1, stride=1,
                              pad=0, instancenorm=instancenorm,
                              spectralnorm=spectralnorm)
        self.toRGB = PGConv2d(out_channels, num_channels, nonlinearity=None,
                              kernel_size=1, pad=0, instancenorm=False,
                              spectralnorm=spectralnorm)

    def forward(self, x, h, last=False):
        """forward.

        Args:
            x (tensor): [batch_size, in_channels, height, width],
                        input tensor.
            h (tensor): [batch_size, in_channels, height, width],
                        tensor skip connected from Generator.
            last (bool): Whether Decoder's last layer or not.

        Returns:
            x (tensor): if last: [batch_size, num_channels, height', width'],
                        else: [batch_size, out_channels, height', width'],
                        output tensor.

        """
        x = torch.cat([x, h], dim=1)
        x = self.conv1(x)
        x = self.conv2(x)
        if last:
            x = self.toRGB(x)
        return x


class Discriminator(nn.Module):
    """Discriminator model class.

    Based on "High Resolution Face Completion with Multiple Controllable
    Attributes via Fully End-to-End Progressive Generative Adversarial
    Networks".
    <https://arxiv.org/abs/1801.07632.pdf>
    """

    def __init__(self,
                 dataset_shape,
                 num_classes,
                 num_layers=2,
                 fmap_base=1024,
                 fmap_min=4,
                 fmap_max=256,
                 latent_size=256,
                 leaky_relu=True,
                 instancenorm=True,
                 spectralnorm=True):
        """constructor.

        Args:
            dataset_shape (list): Shape of input dataset.
            fmap_base (int): Decide the number of network parameter.
            fmap_min (int): Decide the number of network parameter.
            fmap_max (int): Decide the number of network parameter.
            latent_size (int): Latent vector dimension size.
            leaky_relu (bool): Use leaky_relu(True) or ReLU(False)
            instancenorm (bool): Whether use instancenorm or not.
            spectralnorm (bool): Whether use spectralnorm or not.
        """
        super(Discriminator, self).__init__()

        resolution = dataset_shape[-1]
        num_channels = dataset_shape[1]
        self.R = int(np.log2(resolution))
        self.num_layers = num_layers

        nonlinearity = nn.LeakyReLU(0.2) if leaky_relu else nn.ReLU()

        def nf(stage):
            return min(int(fmap_base / (2.0 ** stage)), fmap_max)

        # encoder blocks
        self.encblocks = []
        for i in reversed(range(1, self.R-1)):
            self.encblocks.append(D_EncBlock(nf(i), nf(i-1), num_channels,
                                             nonlinearity, instancenorm,
                                             spectralnorm))
        self.encblocks.append(D_EncLastBlock(latent_size, latent_size,
                                             num_channels, nonlinearity,
                                             instancenorm, spectralnorm))
        self.encblocks = nn.ModuleList(self.encblocks)

        # decoder blocks
        self.decblocks = []
        self.decblocks.append(D_DecFirstBlock(latent_size, latent_size,
                                              num_channels, nonlinearity))
        for i in range(1, self.R-1):
            self.decblocks.append(D_DecBlock(nf(i-1), nf(i), num_channels,
                                             nonlinearity, instancenorm,
                                             spectralnorm))
        self.decblocks = nn.ModuleList(self.decblocks)

        # classifier
        self.dense = Dense(latent_size, spectralnorm=spectralnorm)
        self.pixel_classifier = []
        for i in range(2, 2+self.num_layers):
            self.pixel_classifier.append(PGConv2d(nf(self.R-i), num_classes,
                                                  nonlinearity=nonlinearity,
                                                  instancenorm=instancenorm,
                                                  spectralnorm=spectralnorm))
        self.pixel_classifier = nn.ModuleList(self.pixel_classifier)

    def forward(self, x, cur_level=None):
        """forward.

        Args:
            x (tensor): [batch_size, num_channels, height, width],
                        Input image batch.
            cur_level (int): The level of current training status.

        Returns:
            cls (tensor): [batch_size, num_classes],
                          Predicted prob of each class.

        """
        if cur_level is None:
            cur_level = len(self.encblocks)

        max_level = ceil(cur_level)
        alpha = int(cur_level+1) - cur_level

        # encoder
        hs = []
        if max_level > 1:
            h = self.encblocks[-(max_level)](x, True)
            hs.append(h)
            h = downsample(h, 2)

            if alpha < 1.0:
                x_down = downsample(x, 2)
                skip_connect = self.encblocks[-max_level+1].fromRGB(x_down)
                h = h*alpha + (1-alpha)*skip_connect

            for level in range(max_level-1, 0, -1):
                if level == 1:
                    h, h_prime = self.encblocks[-level](h)
                    hs.append(h_prime)
                else:
                    h = self.encblocks[-level](h)
                    hs.append(h)
                    h = downsample(h, 2)
        else:
            h, h_prime = self.encblocks[-max_level](x, True)
            hs.append(h_prime)

        # classifier
        cls = self.dense(h.squeeze(-1).squeeze(-1))
        # pixel classifier
        pix_cls = None
        if self.R - max_level <= self.num_layers:
            # decoder
            if max_level > 1:
                for level in range(0, max_level-1, 1):
                    if level == 0:
                        h = self.decblocks[level](h, hs[-level-1])
                    else:
                        h = upsample(h, 2)
                        h = self.decblocks[level](h, hs[-level-1])

                x = h  # remember for to_RGB
                h = upsample(h, 2)  # last layer
                h = self.decblocks[max_level-1](h, hs[-level-2])

            else:
                h = self.decblocks[0](h, hs[0])
            pix_cls = self.pixel_classifier[self.R - max_level-1](h)
        return cls, pix_cls
