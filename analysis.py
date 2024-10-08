import os
import sys
import argparse
import datetime
import time
import os.path as osp
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
import numpy as np

import torch
import torch.nn as nn
from torch.optim import lr_scheduler
import torch.backends.cudnn as cudnn
from tqdm import tqdm

# import datasets
import transforms
import torchvision.datasets as datasets
from torch.utils.data import DataLoader
from torch.utils.data import SubsetRandomSampler
import models
import pickle
from utils import AverageMeter, Logger
from center_loss import CenterLoss

parser = argparse.ArgumentParser("Center Loss Example")
# dataset
parser.add_argument('-d', '--dataset', type=str, default='mnist', choices=['mnist'])
parser.add_argument('-j', '--workers', default=4, type=int,
                    help="number of data loading workers (default: 4)")
# optimization
parser.add_argument('--batch-size', type=int, default=128)
parser.add_argument('--lr-model', type=float, default=0.001, help="learning rate for model")
parser.add_argument('--lr-cent', type=float, default=0.5, help="learning rate for center loss")
parser.add_argument('--weight-cent', type=float, default=1, help="weight for center loss")
parser.add_argument('--max-epoch', type=int, default=100)
parser.add_argument('--stepsize', type=int, default=20)
parser.add_argument('--gamma', type=float, default=0.5, help="learning rate decay")
# model
parser.add_argument('--model', type=str, default='cnn')
# misc
parser.add_argument('--eval-freq', type=int, default=10)
parser.add_argument('--print-freq', type=int, default=50)
parser.add_argument('--gpu', type=str, default='0')
parser.add_argument('--seed', type=int, default=1)
parser.add_argument('--use-cpu', action='store_true')
parser.add_argument('--save-dir', type=str, default='log')
parser.add_argument('--plot', action='store_true', help="whether to plot features for every epoch")
# openset
parser.add_argument('--is-filter', type=bool, default=True)

args = parser.parse_args()

def filter_known_unknown(cifar_train):
    filter_ind = []
    filter_ind2 = []
    for i in range(len(cifar_train.targets)):
        c = cifar_train.targets[i]
        if c < 7:
            filter_ind.append(i)
        else:
            filter_ind2.append(i)
    # 随机选
    return filter_ind, filter_ind2

def main():
    torch.manual_seed(args.seed)
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu
    use_gpu = torch.cuda.is_available()
    if args.use_cpu: use_gpu = False

    sys.stdout = Logger(osp.join(args.save_dir, 'log_' + args.dataset + '.txt'))

    if use_gpu:
        print("Currently using GPU: {}".format(args.gpu))
        cudnn.benchmark = True
        torch.cuda.manual_seed_all(args.seed)
    else:
        print("Currently using CPU")

    print("Creating dataset: {}".format(args.dataset))
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,))
    ])
    cifar_train = datasets.MNIST(root='./data/mnist', train=True, download=True, transform=transform)
    cifar_test = datasets.MNIST(root='./data/mnist', train=False, download=True, transform=transform)

    filter_ind, filter_ind2 = filter_known_unknown(cifar_train)

    train_loader_known = DataLoader(cifar_train, batch_size=args.batch_size, shuffle=False, sampler=SubsetRandomSampler(filter_ind))
    train_loader_unknown = DataLoader(cifar_train, batch_size=args.batch_size, shuffle=False, sampler=SubsetRandomSampler(filter_ind2))


    print("Creating model: {}".format(args.model))
    model = models.create(name=args.model, num_classes=7)
    base_model = torch.load('save_model/center_mnist7_baseline_mini.pt')

    if use_gpu:
        model = nn.DataParallel(model).cuda()

    criterion_xent = nn.CrossEntropyLoss()
    criterion_cent = CenterLoss(num_classes=7, feat_dim=2, use_gpu=use_gpu)
    optimizer_model = torch.optim.SGD(model.parameters(), lr=args.lr_model, weight_decay=5e-04, momentum=0.9)
    optimizer_centloss = torch.optim.SGD(criterion_cent.parameters(), lr=args.lr_cent)

    if args.stepsize > 0:
        scheduler = lr_scheduler.StepLR(optimizer_model, step_size=args.stepsize, gamma=args.gamma)

    start_time = time.time()

    for epoch in tqdm(range(1)):
        print("==> Epoch {}/{}".format(epoch+1, args.max_epoch))
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        analysis(train_loader_known, train_loader_unknown, base_model, epoch, 200, device)

    elapsed = round(time.time() - start_time)
    elapsed = str(datetime.timedelta(seconds=elapsed))
    print("Finished. Total elapsed time (h:m:s): {}".format(elapsed))

def analysis(train_loader_known, train_loader_unknown, model, epoch, epochs, device):
    start = time.time()
    model.train()
    train_loss = 0
    correct = 0
    total = 0
    print(" === Epoch: [{}/{}] === ".format(epoch + 1, epochs))
    # logging.info(" === Epoch: [{}/{}] === ".format(epoch + 1, epochs))
    S_ij = {}
    M_ij = {}
    for batch_index, (inputs, targets) in enumerate(train_loader_known):
        inputs, targets = inputs.to(device), targets.to(device)
        _, outputs = model(inputs)
        v_ij, predicted = outputs.max(1)
        m_ij = outputs.mean(1)
        for i in range(len(predicted.data)):
            tmp_class = np.array(predicted.data.cpu())[i]
            tmp_value = np.array(v_ij.data.cpu())[i]
            tmp_mean = np.array(m_ij.data.cpu())[i]
            if tmp_class not in S_ij:
                S_ij[tmp_class] = []
                M_ij[tmp_class] = []
            S_ij[tmp_class].append(tmp_value)
            M_ij[tmp_class].append(tmp_mean)

    unknown_S_ij = {}
    unknown_M_ij = {}
    for batch_index, (inputs, targets) in enumerate(train_loader_unknown):
        inputs, targets = inputs.to(device), targets.to(device)
        _, outputs = model(inputs)
        v_ij, predicted = outputs.max(1)
        m_ij = outputs.mean(1)
        for i in range(len(predicted.data)):
            tmp_class = np.array(predicted.data.cpu())[i]
            tmp_value = np.array(v_ij.data.cpu())[i]
            tmp_mean = np.array(m_ij.data.cpu())[i]
            if tmp_class not in unknown_S_ij:
                unknown_S_ij[tmp_class] = []
                unknown_M_ij[tmp_class] = []
            unknown_S_ij[tmp_class].append(tmp_value)
            unknown_M_ij[tmp_class].append(tmp_mean)

    # 保存结果
    with open("pkl/center_result_mini.pkl", 'wb') as f:
        data = {'known_S': S_ij, 'unknown_S': unknown_S_ij, 'known_M': M_ij, 'unknown_M': unknown_M_ij}
        pickle.dump(data, f)
    f.close()

def train(model, criterion_xent, criterion_cent,
          optimizer_model, optimizer_centloss,
          trainloader, use_gpu, num_classes, epoch):
    model.train()
    xent_losses = AverageMeter()
    cent_losses = AverageMeter()
    losses = AverageMeter()
    
    if args.plot:
        all_features, all_labels = [], []

    for batch_idx, (data, labels) in enumerate(trainloader):
        if use_gpu:
            data, labels = data.cuda(), labels.cuda()
        features, outputs = model(data)
        loss_xent = criterion_xent(outputs, labels)
        loss_cent = criterion_cent(features, labels)
        loss_cent *= args.weight_cent
        loss = loss_xent + loss_cent
        optimizer_model.zero_grad()
        optimizer_centloss.zero_grad()
        loss.backward()
        optimizer_model.step()
        # by doing so, weight_cent would not impact on the learning of centers
        for param in criterion_cent.parameters():
            param.grad.data *= (1. / args.weight_cent)
        optimizer_centloss.step()
        
        losses.update(loss.item(), labels.size(0))
        xent_losses.update(loss_xent.item(), labels.size(0))
        cent_losses.update(loss_cent.item(), labels.size(0))

        if args.plot:
            if use_gpu:
                all_features.append(features.data.cpu().numpy())
                all_labels.append(labels.data.cpu().numpy())
            else:
                all_features.append(features.data.numpy())
                all_labels.append(labels.data.numpy())

        if (batch_idx+1) % args.print_freq == 0:
            print("Batch {}/{}\t Loss {:.6f} ({:.6f}) XentLoss {:.6f} ({:.6f}) CenterLoss {:.6f} ({:.6f})" \
                  .format(batch_idx+1, len(trainloader), losses.val, losses.avg, xent_losses.val, xent_losses.avg, cent_losses.val, cent_losses.avg))

    if args.plot:
        all_features = np.concatenate(all_features, 0)
        all_labels = np.concatenate(all_labels, 0)
        plot_features(all_features, all_labels, num_classes, epoch, prefix='train')

def test(model, testloader, use_gpu, num_classes, epoch):
    model.eval()
    correct, total = 0, 0
    if args.plot:
        all_features, all_labels = [], []

    with torch.no_grad():
        for data, labels in testloader:
            if use_gpu:
                data, labels = data.cuda(), labels.cuda()
            features, outputs = model(data)
            predictions = outputs.data.max(1)[1]
            total += labels.size(0)
            correct += (predictions == labels.data).sum()
            
            if args.plot:
                if use_gpu:
                    all_features.append(features.data.cpu().numpy())
                    all_labels.append(labels.data.cpu().numpy())
                else:
                    all_features.append(features.data.numpy())
                    all_labels.append(labels.data.numpy())

    if args.plot:
        all_features = np.concatenate(all_features, 0)
        all_labels = np.concatenate(all_labels, 0)
        plot_features(all_features, all_labels, num_classes, epoch, prefix='test')

    acc = correct * 100. / total
    err = 100. - acc
    return acc, err

def plot_features(features, labels, num_classes, epoch, prefix):
    """Plot features on 2D plane.

    Args:
        features: (num_instances, num_features).
        labels: (num_instances). 
    """
    colors = ['C0', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8', 'C9']
    for label_idx in range(num_classes):
        plt.scatter(
            features[labels==label_idx, 0],
            features[labels==label_idx, 1],
            c=colors[label_idx],
            s=1,
        )
    plt.legend(['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'], loc='upper right')
    dirname = osp.join(args.save_dir, prefix)
    if not osp.exists(dirname):
        os.mkdir(dirname)
    save_name = osp.join(dirname, 'epoch_' + str(epoch+1) + '.png')
    plt.savefig(save_name, bbox_inches='tight')
    plt.close()

if __name__ == '__main__':
    main()





