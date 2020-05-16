import math
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models
import collections
import math

device = torch.device("cuda")
def init_weights(m):
    if isinstance(m, nn.Conv2d) or isinstance(m, nn.Linear):
        m.weight.data.normal_(0, 1e-3)
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.ConvTranspose2d):
        m.weight.data.normal_(0, 1e-3)
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.BatchNorm2d):
        m.weight.data.fill_(1)
        m.bias.data.zero_()

def conv_bn_relu(in_channels, out_channels, kernel_size, \
        stride=1, padding=0, bn=True, relu=True):
    bias = not bn
    layers = []
    layers.append(
        nn.Conv2d(in_channels,
                  out_channels,
                  kernel_size,
                  stride,
                  padding,
                  bias=bias))
    if bn:
        layers.append(nn.BatchNorm2d(out_channels))
    if relu:
        layers.append(nn.LeakyReLU(0.2, inplace=True))
    layers = nn.Sequential(*layers)

    # initialize the weights
    for m in layers.modules():
        init_weights(m)

    return layers

def convt_bn_relu(in_channels, out_channels, kernel_size, \
        stride=1, padding=0, output_padding=0, bn=True, relu=True):
    bias = not bn
    layers = []
    layers.append(
        nn.ConvTranspose2d(in_channels,
                           out_channels,
                           kernel_size,
                           stride,
                           padding,
                           output_padding,
                           bias=bias))
    if bn:
        layers.append(nn.BatchNorm2d(out_channels))
    if relu:
        layers.append(nn.LeakyReLU(0.2, inplace=True))
    layers = nn.Sequential(*layers)

    # initialize the weights
    for m in layers.modules():
        init_weights(m)

    return layers

class Unpool(nn.Module):
    # Unpool: 2*2 unpooling with zero padding
    #Why num_channels and grouping??
    def __init__(self, num_channels, stride=2):
        super(Unpool, self).__init__()

        self.num_channels = num_channels
        self.stride = stride

        # create kernel [1, 0; 0, 0]
        self.weights = torch.autograd.Variable(torch.zeros(num_channels, 1, stride, stride).to(device)) # currently not compatible with running on CPU
        #Variable currently deprecated
        self.weights[:,:,0,0] = 1

    def forward(self, x):
        return F.conv_transpose2d(x, self.weights, stride=self.stride, groups=self.num_channels)

def weights_init(m):
    # Initialize filters with Gaussian random weights
    if isinstance(m, nn.Conv2d):
        n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
        m.weight.data.normal_(0, math.sqrt(2. / n)) #Why variance is initialized like this??
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.ConvTranspose2d):
        n = m.kernel_size[0] * m.kernel_size[1] * m.in_channels
        m.weight.data.normal_(0, math.sqrt(2. / n))
        if m.bias is not None:
            m.bias.data.zero_()
    elif isinstance(m, nn.BatchNorm2d):
        m.weight.data.fill_(1)
        m.bias.data.zero_()

class Decoder(nn.Module):
    # Decoder is the base class for all decoders

    names = ['deconv2', 'deconv3', 'upconv', 'upproj']

    def __init__(self):
        super(Decoder, self).__init__()

        self.layer1 = None
        self.layer2 = None
        self.layer3 = None
        self.layer4 = None

    def forward(self, x):
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        return x

class DeConv(Decoder):
    def __init__(self, in_channels, kernel_size):
        assert kernel_size>=2, "kernel_size out of range: {}".format(kernel_size)
        super(DeConv, self).__init__()

        def convt(in_channels):
            stride = 2
            padding = (kernel_size - 1) // 2
            output_padding = kernel_size % 2
            assert -2 - 2*padding + kernel_size + output_padding == 0, "deconv parameters incorrect"

            module_name = "deconv{}".format(kernel_size)
            return nn.Sequential(collections.OrderedDict([
                  (module_name, nn.ConvTranspose2d(in_channels,in_channels//2,kernel_size,
                        stride,padding,output_padding,bias=False)),
                  ('batchnorm', nn.BatchNorm2d(in_channels//2)),
                  ('relu',      nn.ReLU(inplace=True)),
                ]))

        self.layer1 = convt(in_channels)
        self.layer2 = convt(in_channels // 2)
        self.layer3 = convt(in_channels // (2 ** 2))
        self.layer4 = convt(in_channels // (2 ** 3))

class UpConv(Decoder):
    # UpConv decoder consists of 4 upconv modules with decreasing number of channels and increasing feature map size
    def upconv_module(self, in_channels):
        # UpConv module: unpool -> 5*5 conv -> batchnorm -> ReLU
        upconv = nn.Sequential(collections.OrderedDict([
          ('unpool',    Unpool(in_channels)),
          ('conv',      nn.Conv2d(in_channels,in_channels//2,kernel_size=5,stride=1,padding=2,bias=False)),
          ('batchnorm', nn.BatchNorm2d(in_channels//2)),
          ('relu',      nn.ReLU()),
        ]))
        return upconv

    def __init__(self, in_channels):
        super(UpConv, self).__init__()
        self.layer1 = self.upconv_module(in_channels)
        self.layer2 = self.upconv_module(in_channels//2)
        self.layer3 = self.upconv_module(in_channels//4)
        self.layer4 = self.upconv_module(in_channels//8)

class UpProj(Decoder):
    # UpProj decoder consists of 4 upproj modules with decreasing number of channels and increasing feature map size

    class UpProjModule(nn.Module):
        # UpProj module has two branches, with a Unpool at the start and a ReLu at the end
        #   upper branch: 5*5 conv -> batchnorm -> ReLU -> 3*3 conv -> batchnorm
        #   bottom branch: 5*5 conv -> batchnorm

        def __init__(self, in_channels):
            super(UpProj.UpProjModule, self).__init__()
            out_channels = in_channels//2
            self.unpool = Unpool(in_channels)
            self.upper_branch = nn.Sequential(collections.OrderedDict([
              ('conv1',      nn.Conv2d(in_channels,out_channels,kernel_size=5,stride=1,padding=2,bias=False)),
              ('batchnorm1', nn.BatchNorm2d(out_channels)),
              ('relu',      nn.ReLU()),
              ('conv2',      nn.Conv2d(out_channels,out_channels,kernel_size=3,stride=1,padding=1,bias=False)),
              ('batchnorm2', nn.BatchNorm2d(out_channels)),
            ]))
            self.bottom_branch = nn.Sequential(collections.OrderedDict([
              ('conv',      nn.Conv2d(in_channels,out_channels,kernel_size=5,stride=1,padding=2,bias=False)),
              ('batchnorm', nn.BatchNorm2d(out_channels)),
            ]))
            self.relu = nn.ReLU()

        def forward(self, x):
            x = self.unpool(x)
            x1 = self.upper_branch(x)
            x2 = self.bottom_branch(x)
            x = x1 + x2
            x = self.relu(x)
            return x

    def __init__(self, in_channels):
        super(UpProj, self).__init__()
        self.layer1 = self.UpProjModule(in_channels)
        self.layer2 = self.UpProjModule(in_channels//2)
        self.layer3 = self.UpProjModule(in_channels//4)
        self.layer4 = self.UpProjModule(in_channels//8)

def choose_decoder(decoder, in_channels):
    # iheight, iwidth = 10, 8
    if decoder[:6] == 'deconv':
        assert len(decoder)==7
        kernel_size = int(decoder[6])
        return DeConv(in_channels, kernel_size)
    elif decoder == "upproj":
        return UpProj(in_channels)
    elif decoder == "upconv":
        return UpConv(in_channels)
    else:
        assert False, "invalid option for decoder: {}".format(decoder)

class VGGNet(nn.Module):
    # def __init__(self, layers, decoder, output_size, in_channels=3, pretrained=True):
    def __init__(self,args):
        if args.layers not in [16,19]:
            raise RuntimeError('Only 16 and 19 layer model is defined for VGGNet. Got {}'.format(args.layers))
        in_channels = len(args.input)
        super(VGGNet, self).__init__()
        if(args.layers==16):
          pretrained_model = torchvision.models.vgg16_bn(pretrained=args.pretrained)
        else:
          pretrained_model = torchvision.models.vgg19_bn(pretrained=args.pretrained)
        self.features = pretrained_model._modules['features']
        if in_channels != 3:
            new_features = nn.Sequential(*list(self.features.children()))
            conv2d = nn.Conv2d(in_channels, 64, kernel_size=3, padding=1)
            weights_init(conv2d)
            new_features[0] = conv2d
            self.features = new_features

        self.output_size = (375,1242)

        # clear memory
        del pretrained_model

        # define number of intermediate channels
        if args.layers == 16:
            num_channels = 512
        elif args.layers == 19:
            num_channels = 512
        self.conv2 = nn.Conv2d(num_channels,num_channels//2,kernel_size=1,bias=False)
        self.bn2 = nn.BatchNorm2d(num_channels//2)
        self.decoder = choose_decoder(args.decoder, num_channels//2)

        # setting bias=true doesn't improve accuracy
        self.conv3 = nn.Conv2d(num_channels//32,1,kernel_size=3,stride=1,padding=1,bias=False)
        self.upsample = nn.Upsample(size=self.output_size)

        # weight init
        self.conv2.apply(weights_init)
        self.bn2.apply(weights_init)
        self.decoder.apply(weights_init)
        self.conv3.apply(weights_init)
    def forward(self, x):
        # vggnet
        # print('printing in forward')
        # print(x.size())
        x = self.features(x)
        # print(x.size())
        x = self.conv2(x)
        # print(x.size())
        x = self.bn2(x)
        # print(x.size())

        # decoder
        x = self.decoder(x)
        # print(x.size())
        x = self.conv3(x)
        # print(x.size())
        x = self.upsample(x)
        # print(x.size())
        # x = x.view(1,375,1242)
        return x
# class DepthCompletionNet(nn.Module):
#     def __init__(self, args):
#         assert (
#             args.layers in [18, 34, 50, 101, 152]
#         ), 'Only layers 18, 34, 50, 101, and 152 are defined, but got {}'.format(
#             layers)
#         super(DepthCompletionNet, self).__init__()
#         self.modality = args.input

#         if 'd' in self.modality:
#             channels = 64 // len(self.modality)
#             self.conv1_d = conv_bn_relu(1,
#                                         channels,
#                                         kernel_size=3,
#                                         stride=1,
#                                         padding=1)
#         if 'rgb' in self.modality:
#             channels = 64 * 3 // len(self.modality)
#             self.conv1_img = conv_bn_relu(3,
#                                           channels,
#                                           kernel_size=3,
#                                           stride=1,
#                                           padding=1)
#         elif 'g' in self.modality:
#             channels = 64 // len(self.modality)
#             self.conv1_img = conv_bn_relu(1,
#                                           channels,
#                                           kernel_size=3,
#                                           stride=1,
#                                           padding=1)

#         pretrained_model = resnet.__dict__['resnet{}'.format(
#             args.layers)](pretrained=args.pretrained)
#         if not args.pretrained:
#             pretrained_model.apply(init_weights)
#         #self.maxpool = pretrained_model._modules['maxpool']
#         self.conv2 = pretrained_model._modules['layer1']
#         self.conv3 = pretrained_model._modules['layer2']
#         self.conv4 = pretrained_model._modules['layer3']
#         self.conv5 = pretrained_model._modules['layer4']
#         del pretrained_model  # clear memory

#         # define number of intermediate channels
#         if args.layers <= 34:
#             num_channels = 512
#         elif args.layers >= 50:
#             num_channels = 2048
#         self.conv6 = conv_bn_relu(num_channels,
#                                   512,
#                                   kernel_size=3,
#                                   stride=2,
#                                   padding=1)

#         # decoding layers
#         kernel_size = 3
#         stride = 2
#         self.convt5 = convt_bn_relu(in_channels=512,
#                                     out_channels=256,
#                                     kernel_size=kernel_size,
#                                     stride=stride,
#                                     padding=1,
#                                     output_padding=1)
#         self.convt4 = convt_bn_relu(in_channels=768,
#                                     out_channels=128,
#                                     kernel_size=kernel_size,
#                                     stride=stride,
#                                     padding=1,
#                                     output_padding=1)
#         self.convt3 = convt_bn_relu(in_channels=(256 + 128),
#                                     out_channels=64,
#                                     kernel_size=kernel_size,
#                                     stride=stride,
#                                     padding=1,
#                                     output_padding=1)
#         self.convt2 = convt_bn_relu(in_channels=(128 + 64),
#                                     out_channels=64,
#                                     kernel_size=kernel_size,
#                                     stride=stride,
#                                     padding=1,
#                                     output_padding=1)
#         self.convt1 = convt_bn_relu(in_channels=128,
#                                     out_channels=64,
#                                     kernel_size=kernel_size,
#                                     stride=1,
#                                     padding=1)
#         self.convtf = conv_bn_relu(in_channels=128,
#                                    out_channels=1,
#                                    kernel_size=1,
#                                    stride=1,
#                                    bn=False,
#                                    relu=False)

#     def forward(self, x):
#         # first layer
#         if 'd' in self.modality:
#             conv1_d = self.conv1_d(x['d'])
#         if 'rgb' in self.modality:
#             conv1_img = self.conv1_img(x['rgb'])
#         elif 'g' in self.modality:
#             conv1_img = self.conv1_img(x['g'])

#         if self.modality == 'rgbd' or self.modality == 'gd':
#             conv1 = torch.cat((conv1_d, conv1_img), 1)
#         else:
#             conv1 = conv1_d if (self.modality == 'd') else conv1_img

#         conv2 = self.conv2(conv1)
#         conv3 = self.conv3(conv2)  # batchsize * ? * 176 * 608
#         conv4 = self.conv4(conv3)  # batchsize * ? * 88 * 304
#         conv5 = self.conv5(conv4)  # batchsize * ? * 44 * 152
#         conv6 = self.conv6(conv5)  # batchsize * ? * 22 * 76

#         # decoder
#         convt5 = self.convt5(conv6)
#         y = torch.cat((convt5, conv5), 1)

#         convt4 = self.convt4(y)
#         y = torch.cat((convt4, conv4), 1)

#         convt3 = self.convt3(y)
#         y = torch.cat((convt3, conv3), 1)

#         convt2 = self.convt2(y)
#         y = torch.cat((convt2, conv2), 1)

#         convt1 = self.convt1(y)
#         y = torch.cat((convt1, conv1), 1)

#         y = self.convtf(y)

#         if self.training:
#             return 100 * y
#         else:
#             min_distance = 0.9
#             return F.relu(
#                 100 * y - min_distance
#             ) + min_distance  # the minimum range of Velodyne is around 3 feet ~= 0.9m
