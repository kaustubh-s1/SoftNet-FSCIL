import torch
import torch.nn as nn
import numpy as np
from .signet import SignetConv2d, SignetLinear
from torch.nn.functional import relu, avg_pool2d

from collections import OrderedDict

# Multiple Input Sequential
class mySequential(nn.Sequential):
    def forward(self, *inputs):
        mask = inputs[1]
        mode = inputs[2]
        sparsity = inputs[3]
        inputs = inputs[0]
        for module in self._modules.values():
            if isinstance(module, SignetBasicBlock):
                inputs = module(inputs, mask, mode, sparsity)
            else:
                inputs = module(inputs)

        return inputs

def signet_conv3x3(in_planes, out_planes, stride=1, sparsity=None, kernel_size=3, gamma=0.9):
    return SignetConv2d(in_planes, out_planes, kernel_size=kernel_size, stride=stride,
                        padding=1, bias=False, sparsity=sparsity, gamma=gamma)

class SignetBasicBlock(nn.Module):
    expansion = 1
    def __init__(self, in_planes, planes, stride=1, sparsity=0.5,
                 gamma=None, name="",
                 last=False, track=True, affine=True):
        super(SignetBasicBlock, self).__init__()

        self.gamma = gamma
        self.sparsity = sparsity
        self.alpha = alpha
        self.name = name
        self.conv1 = signet_conv3x3(in_planes, planes, stride,
                                    sparsity=self.sparsity[0], gamma=self.gamma[0])
        self.bn1 = nn.BatchNorm2d(planes, track_running_stats=track, affine=affine)
        self.conv2 = signet_conv3x3(planes, planes,
                                    sparsity=self.sparsity[1], gamma=self.gamma[1])
        self.bn2 = nn.BatchNorm2d(planes, track_running_stats=track, affine=affine)
        self.relu = nn.ReLU(inplace=True)

        # Shortcut
        self.shortcut = None
        if stride != 1 or in_planes != self.expansion * planes:
            self.shortcut = True

            if False:
                self.conv3 = SignetConv2d(in_planes, self.expansion * planes, kernel_size=1,
                                          stride=stride, bias=False, sparsity=self.sparsity[2])
                self.bn3 = nn.BatchNorm2d(self.expansion * planes, track_running_stats=track,
                                          affine=affine)
            else:
                self.downsample = mySequential(
                    SignetConv2d(in_planes, self.expansion * planes, kernel_size=1,
                                 stride=stride, bias=False, sparsity=self.sparsity[2], gamma=self.gamma[2]),
                    nn.BatchNorm2d(self.expansion * planes, track_running_stats=track, affine=affine)
                )


        self.count = 0

    def forward(self, x, mask, mode, sparsity):

        if sparsity is not None:
            self.sparsity = sparsity

        residual = x
        name = self.name + ".conv1"
        out = relu(self.bn1(self.conv1(x, weight_mask=mask[name+'.weight'],
                                       bias_mask=mask[name+'.bias'], mode=mode,
                                       sparsity=self.sparsity[0])))
        name = self.name + ".conv2"
        out = self.bn2(self.conv2(out, weight_mask=mask[name+'.weight'],
                                  bias_mask=mask[name+'.bias'], mode=mode,
                                  sparsity=self.sparsity[1]))
        if self.shortcut :
            if False:
                name = self.name + ".conv3"
                residual = self.bn3(self.conv3(x, weight_mask=mask[name+'.weight'],
                                               bias_mask=mask[name+'.bias'], mode=mode,
                                               sparsity=self.sparsity[2]))
            else:
                name = self.name + ".downsample.0"
                residual = self.downsample(x,mask[name+'.weight'],mode,self.sparsity[2])

        out += residual
        out = self.relu(out)
        return out


class SignetResNet(nn.Module):
    def __init__(self, block, num_blocks, sparsity,
                 num_classes=100, adopt_classifier=True,
                 flatten=True, track=True, affine=True, gamma=0.9):
        super(SignetResNet, self).__init__()

        self.act=OrderedDict()
        self.adopt_classifier = adopt_classifier
        self.flatten = flatten
        self.alpha = None
        self.sparsity = {'conv1': sparsity,
                         'layer1': [[sparsity, sparsity, sparsity],
                                    [sparsity, sparsity],
                                    [sparsity, sparsity]],
                         'layer2': [[sparsity, sparsity, sparsity],
                                    [sparsity, sparsity],
                                    [sparsity, sparsity]],
                         'layer3': [[sparsity, sparsity, sparsity],
                                    [sparsity, sparsity],
                                    [sparsity, sparsity]]}


        self.gamma = {'conv1': gamma,
                      'layer1': [[gamma, gamma, gamma],
                                 [gamma, gamma],
                                 [gamma, gamma]],
                      'layer2': [[gamma, gamma, gamma],
                                 [gamma, gamma],
                                 [gamma, gamma]],
                      'layer3': [[gamma, gamma, gamma],
                                 [gamma, gamma],
                                 [gamma, gamma]]}

        block_size=[16, 32, 64]
        self.in_planes = block_size[0]
        self.conv1 = signet_conv3x3(3, block_size[0], 1,
                                    sparsity=self.sparsity['conv1'],
                                    gamma=self.gamma['conv1'])

        self.bn1 = nn.BatchNorm2d(block_size[0],
                                  track_running_stats=track, affine=affine)
        self.relu = nn.ReLU(inplace=True)

        self.layer1 = self._make_layer(block, block_size[0], num_blocks[0],
                                       stride=1,
                                       sparsity=self.sparsity['layer1'],
                                       gamma=self.gamma['layer1'],
                                       name="layer1",
                                       track=track, affine=affine)

        self.layer2 = self._make_layer(block, block_size[1], num_blocks[1],
                                       stride=2,
                                       sparsity=self.sparsity['layer2'],
                                       gamma=self.gamma['layer2'],
                                       name="layer2",
                                       track=track, affine=affine)

        self.layer3 = self._make_layer(block, block_size[2], num_blocks[2],
                                       stride=2,
                                       sparsity=self.sparsity['layer3'],
                                       gamma=self.gamma['layer3'],
                                       name="layer3",
                                       last_phase=True,
                                       track=track, affine=affine)

        self.avg_pool = nn.AdaptiveAvgPool2d((1, 1))
        self.mask_fc = True
        if self.adopt_classifier:
            if self.mask_fc:
                self.fc = SignetLinear(block_size[2] * block.expansion,
                                       num_classes,
                                       sparsity=sparsity,
                                       gamma=gamma,
                                       bias=False)
            else:
                self.fc = nn.Linear(block_size[2] * block.expansion, num_classes)

        else:
            if False:
                self.fc = SignetLinear(block_size[2] * block.expansion,
                                       num_classes,
                                       sparsity=sparsity, alpha=1.0,
                                       bias=False)
            else:
                # define score function
                self.fc = nn.Linear(block_size[2] * block.expansion,
                                    2, bias=False)

        nn.init.normal_(self.fc.weight, std=0.001)
        if not self.mask_fc:
            nn.init.constant_(self.fc.bias, 0)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm2d) and track:
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        # Constant none_masks
        self.none_masks = {}
        for name, module in self.named_modules():
            if isinstance(module, SignetLinear) or isinstance(module, SignetConv2d):
                self.none_masks[name + '.weight'] = None
                self.none_masks[name + '.bias'] = None

    def _make_layer(self, block, planes, num_blocks, stride, sparsity, gamma, name,
                    last_phase=False, track=True, affine=True):
        strides = [stride] + [1] * (num_blocks - 1)
        layers = []
        name_count = 0
        new_name = name + "." + str(name_count)
        layers.append(block(self.in_planes, planes, stride, sparsity[0], gamma=gamma[0],
                            #shortcut=True,
                            name=new_name, track=track, affine=affine))

        name_count += 1
        #for stride in strides:
        self.in_planes = planes * block.expansion
        if last_phase:
            for i in range(1, num_blocks-1):
                new_name = name + "." + str(name_count)
                layers.append(block(self.in_planes, planes, stride=1,
                                    sparsity=sparsity[i], gamma=gamma[i],
                                    name=new_name,
                                    track=track, affine=affine))
                name_count += 1
            new_name = name + "." + str(name_count)
            layers.append(block(self.in_planes, planes, stride=1,
                                sparsity=sparsity[i+1], gamma=gamma[i+1],
                                name=new_name,
                                last=True, track=track, affine=affine))
        else:
            for i in range(1, num_blocks):
                new_name = name + "." + str(name_count)
                layers.append(block(self.in_planes, planes, stride=1,
                                    sparsity=sparsity[i], gamma=gamma[i],
                                    name=new_name,
                                    track=track, affine=affine))
                name_count += 1

        return mySequential(*layers)

    def forward(self, x, mask=None, mode="train", sparsity=None):
        if mask is None:
            mask = self.none_masks

        if sparsity is None:
            sparsity = {'conv1': None,
                        'layer1': [None, None, None],
                        'layer2': [None, None, None],
                        'layer3': [None, None, None, None],
                        'fc': None}
        else:
            sparsity = {'conv1': None,
                        'layer1': [None, None, None],
                        'layer2': [None, None, None],
                        'layer3': [True, True, True, True],
                        'fc': None}


        self.act['conv1']=x
        x = self.conv1(x, weight_mask=mask['conv1.weight'], bias_mask=mask['conv1.bias'],
                       mode=mode, sparsity=sparsity['conv1'])

        self.act['bn1']=x
        x = self.bn1(x)
        self.act['relu']=x
        x = self.relu(x)

        self.act['layer1']=x
        x = self.layer1(x, mask, mode, sparsity['layer1'])
        self.act['layer2']=x
        x = self.layer2(x, mask, mode, sparsity['layer2'])
        self.act['layer3']=x
        x = self.layer3(x, mask, mode, sparsity['layer3'])

        self.act['avg_pool']=x
        output = self.avg_pool(x)

        self.act['fc']=output

        if self.flatten:
            output = output.view(output.size(0), -1)
            if self.adopt_classifier:
                if self.mask_fc:
                    output = self.fc(output,weight_mask=mask['fc.weight'],
                                     bias_mask=mask['fc.bias'],mode=mode,
                                     sparsity=sparsity['fc'])
                else:
                    output = self.fc(output)

        return output

    def forward_score(self, x, mask=None, mode="train", sparsity=None, task_id=0):
        if mask is None:
            mask = self.none_masks

        if sparsity is None:
            sparsity = {'conv1': None,
                        'layer1': [None, None, None],
                        'layer2': [None, None, None],
                        'layer3': [None, None, None, None],
                        'fc': None}

        else:
            sparsity = {'conv1': True,
                        'layer1': [True, True, True],
                        'layer2': [True, True, True],
                        'layer3': [True, True, True],
                        'fc': True}

        self.act['conv1']=x
        x = self.conv1(x, weight_mask=mask['conv1.weight'], bias_mask=mask['conv1.bias'],
                       mode=mode, sparsity=sparsity['conv1'])

        self.act['bn1']=x
        x = self.bn1(x)
        self.act['relu']=x
        x = self.relu(x)

        self.act['layer1']=x
        x = self.layer1(x, mask, mode, sparsity['layer1'])
        self.act['layer2']=x
        x = self.layer2(x, mask, mode, sparsity['layer2'])
        self.act['layer3']=x
        x = self.layer3(x, mask, mode, sparsity['layer3'])

        self.act['avg_pool']=x
        output = self.avg_pool(x)

        self.act['fc']=output

        if self.flatten:
            output = output.view(output.size(0), -1)
            if self.adopt_classifier:
                if self.mask_fc:
                    output_fc = self.fc(output,weight_mask=mask['fc.weight'],
                                        bias_mask=mask['fc.bias'],mode=mode,
                                        sparsity=sparsity['fc'])
                else:
                    output_fc = self.fc(output)

        return output, output_fc

    def forward_fc(self, x, mask=None, mode="train", sparsity=None, task_id=1):
        if mask is None:
            mask = self.none_masks

        if sparsity is None:
            sparsity = {'conv1': None,
                        'layer1': [None, None, None],
                        'layer2': [None, None, None],
                        'layer3': [None, None, None, None],
                        'fc': None}

        self.act['conv1']=x
        x = self.conv1(x, weight_mask=mask['conv1.weight'], bias_mask=mask['conv1.bias'],
                       mode=mode, sparsity=sparsity)

        self.act['bn1']=x
        x = self.bn1(x)
        self.act['relu']=x
        x = self.relu(x)

        self.act['layer1']=x
        x = self.layer1(x, mask, mode, sparsity)
        self.act['layer2']=x
        x = self.layer2(x, mask, mode, sparsity)
        self.act['layer3']=x
        x = self.layer3(x, mask, mode, sparsity)

        self.act['avg_pool']=x
        output = self.avg_pool(x)

        self.act['fc']=output

        if self.flatten:
            output = output.view(output.size(0), -1)
            output_fc = output
        return output, output_fc

    def forward_without_cf(self, x, mask=None, mode="train", sparsity=None):
        if mask is None:
            mask = self.none_masks
        x = self.conv1(x, weight_mask=mask['conv1.weight'], bias_mask=mask['conv1.bias'],
                       mode=mode, sparsity=sparsity)
        x = self.bn1(x)
        x = self.relu(x)

        x = self.layer1(x, mask, mode, sparsity=sparsity)
        x = self.layer2(x, mask, mode, sparsity=sparsity)
        x = self.layer3(x, mask, mode, sparsity=sparsity)

        output = self.avg_pool(x)

        output = output.view(output.size(0), -1)

        return output

    def forward_o_embeddings(self, x, mask=None, mode="train", sparsity=None):
        if mask is None:
            mask = self.none_masks
        x = self.conv1(x, weight_mask=mask['conv1.weight'], bias_mask=mask['conv1.bias'],
                       mode=mode, sparsity=sparsity)
        x = self.bn1(x)
        x = self.relu(x)

        x = self.layer1(x, mask, mode, sparsity=sparsity)
        x = self.layer2(x, mask, mode, sparsity=sparsity)
        x = self.layer3(x, mask, mode, sparsity=sparsity)

        output = self.avg_pool(x)

        if self.flatten:
            output = output.view(output.size(0), -1)
            if self.adopt_classifier:
                if self.mask_fc:
                    y = self.fc(output,weight_mask=mask['fc.weight'],bias_mask=mask['fc.bias'],mode=mode, sparsity=sparsity)
                else:
                    output = self.fc(output)

        return output, y

    def get_masks(self, task_id=0):
        task_mask = {}
        for name, module in self.named_modules():
            # For the time being we only care about the current task outputhead
            if 'last' in name:
                if name != 'last.' + str(task_id):
                    continue

            if isinstance(module, SignetLinear) or isinstance(module, SignetConv2d):
                task_mask[name + '.weight'] = module.weight_mask.detach().clone().type(torch.float)

                if getattr(module, 'bias') is not None:
                    task_mask[name + '.bias'] = module.bias_mask.detach().clone().type(torch.float)
                else:
                    task_mask[name + '.bias'] = None

        return task_mask

class SigResnet20_cifar_small(nn.Module):
    def __init__(self, num_classes=100, adopt_classifier=True, flatten=True, sparsity=0.1, gamma=0.9):
        super(SigResnet20_cifar_small, self).__init__()

        track = True
        affine = True

        self.func = SignetResNet(SignetBasicBlock, [3, 3, 3],
                                 sparsity=sparsity,
                                 num_classes=num_classes,
                                 adopt_classifier=adopt_classifier,
                                 flatten=flatten, track=track,
                                 affine=affine, gamma=gamma)

    def forward(self, x, mask=None, mode="train", sparsity=None):
        return self.func(x, mask, mode, sparsity)

    def forward_score(self, x, mask=None, mode="train", sparsity=None):
        return self.func.forward_score(x, mask, mode, sparsity)

    def forward_fc(self, x, mask=None, mode="train", sparsity=None):
        return self.func.forward_fc(x, mask, mode, sparsity)

    def forward_without_cf(self, x, mask=None, mode="train", sparsity=None):
        return self.func.forward_without_cf(x, mask, mode, sparsity)

    def forward_o_embeddings(self, x, mask=None, mode="train", sparsity=None):
        return self.func.forward_o_embeddings(x, mask, mode, sparsity)

    def get_masks(self, task_id):
        return self.func.get_masks(task_id)