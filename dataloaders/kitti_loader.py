import os
import os.path
import glob
import fnmatch  # pattern matching
import numpy as np
from numpy import linalg as LA
from random import choice
from PIL import Image
import torch
import torch.utils.data as data
import cv2
from dataloaders import transforms
from dataloaders.dense_to_sparse import UniformSampling, SimulatedStereo, RandomSampling
from dataloaders.pose_estimator import get_pose_pnp
import h5py #https://www.h5py.org/
input_options = ['d', 'rgb', 'rgbd', 'g', 'gd']

IMG_EXTENSIONS = ['.h5', '.png']

def is_image_file(filename):
    return any(os.path.splitext(filename)[-1] == extension for extension in IMG_EXTENSIONS)

def make_dataset_h5(dir):
    images = []
    dir = os.path.expanduser(dir)
    for target in sorted(os.listdir(dir)):
        d = os.path.join(dir, target)
        if not os.path.isdir(d):
            continue
        for root, _, fnames in sorted(os.walk(d)):
            for fname in sorted(fnames):
                if is_image_file(fname):
                    path = os.path.join(root, fname)
                    images.append(path)
    return np.array(images)

def make_dataset_png(dir):
    images = []
    dir = os.path.expanduser(dir)
    dir_depth = os.path.join(dir,'depth')
    for root, _, fnames in sorted(os.walk(dir_depth)):
        for fname in sorted(fnames):
            if is_image_file(fname):
                depth_path = os.path.join(root, fname)
                rgb_path = os.path.join(root.replace('depth', 'rgb'), fname)
                item = (rgb_path, depth_path)
                images.append(item) #list of tuples
    return np.array(images)

def png_loader(rgb_path, depth_path):
    rgb = cv2.imread(rgb_path) if rgb_path is not None else None
    depth = cv2.imread(depth_path, 0) if depth_path is not None else None
    #rgb = np.transpose(rgb, (1, 2, 0))  #HXWXC
    return rgb, depth

def h5_loader(path):

    if path is None:
        return None, None

    h5f = h5py.File(path, "r")
    rgb = np.array(h5f['rgb'])
    rgb = np.transpose(rgb, (1, 2, 0)) #HXWXC
    depth = np.array(h5f['depth'])
    return rgb, depth

def load_calib():
    """
    Temporarily hardcoding the calibration matrix using calib file from 2011_09_26
    """
    calib = open("dataloaders/calib_cam_to_cam.txt", "r")
    lines = calib.readlines()
    P_rect_line = lines[25]

    Proj_str = P_rect_line.split(":")[1].split(" ")[1:]
    Proj = np.reshape(np.array([float(p) for p in Proj_str]),
                      (3, 4)).astype(np.float32)
    K = Proj[:3, :3]  # camera matrix

    # note: we will take the center crop of the images during augmentation
    # that changes the optical centers, but not focal lengths
    K[0, 2] = K[
        0,
        2] - 13  # from width = 1242 to 1216, with a 13-pixel cut on both sides
    K[1, 2] = K[
        1,
        2] - 11.5  # from width = 375 to 352, with a 11.5-pixel cut on both sides
    return K


# def get_paths_and_transform(split, args):
#     assert (args.use_d or args.use_rgb
#             or args.use_g), 'no proper input selected'

#     if split == "train":
#         transform = train_transform
#         glob_d = os.path.join(
#             args.data_folder,
#             'data_depth_velodyne/train/*_sync/proj_depth/velodyne_raw/image_0[2,3]/*.png'
#         )
#         glob_gt = os.path.join(
#             args.data_folder,
#             'data_depth_annotated/train/*_sync/proj_depth/groundtruth/image_0[2,3]/*.png'
#         )

#         def get_rgb_paths(p):
#             ps = p.split('/')
#             pnew = '/'.join([args.data_folder] + ['data_rgb'] + ps[-6:-4] +
#                             ps[-2:-1] + ['data'] + ps[-1:])
#             return pnew
#     elif split == "val":
#         if args.val == "full":
#             transform = val_transform
#             glob_d = os.path.join(
#                 args.data_folder,
#                 'data_depth_velodyne/val/*_sync/proj_depth/velodyne_raw/image_0[2,3]/*.png'
#             )
#             glob_gt = os.path.join(
#                 args.data_folder,
#                 'data_depth_annotated/val/*_sync/proj_depth/groundtruth/image_0[2,3]/*.png'
#             )
#             def get_rgb_paths(p):
#                 ps = p.split('/')
#                 pnew = '/'.join(ps[:-7] +
#                     ['data_rgb']+ps[-6:-4]+ps[-2:-1]+['data']+ps[-1:])
#                 return pnew
#         elif args.val == "select":
#             transform = no_transform
#             glob_d = os.path.join(
#                 args.data_folder,
#                 "depth_selection/val_selection_cropped/velodyne_raw/*.png")
#             glob_gt = os.path.join(
#                 args.data_folder,
#                 "depth_selection/val_selection_cropped/groundtruth_depth/*.png"
#             )
#             def get_rgb_paths(p):
#                 return p.replace("groundtruth_depth","image")
#     elif split == "test_completion":
#         transform = no_transform
#         glob_d = os.path.join(
#             args.data_folder,
#             "depth_selection/test_depth_completion_anonymous/velodyne_raw/*.png"
#         )
#         glob_gt = None  #"test_depth_completion_anonymous/"
#         glob_rgb = os.path.join(
#             args.data_folder,
#             "depth_selection/test_depth_completion_anonymous/image/*.png")
#     elif split == "test_prediction":
#         transform = no_transform
#         glob_d = None
#         glob_gt = None  #"test_depth_completion_anonymous/"
#         glob_rgb = os.path.join(
#             args.data_folder,
#             "depth_selection/test_depth_prediction_anonymous/image/*.png")
#     else:
#         raise ValueError("Unrecognized split " + str(split))

#     if glob_gt is not None:
#         # train or val-full or val-select
#         paths_d = sorted(glob.glob(glob_d))
#         paths_gt = sorted(glob.glob(glob_gt))
#         paths_rgb = [get_rgb_paths(p) for p in paths_gt]
#     else:
#         # test only has d or rgb
#         paths_rgb = sorted(glob.glob(glob_rgb))
#         paths_gt = [None] * len(paths_rgb)
#         if split == "test_prediction":
#             paths_d = [None] * len(
#                 paths_rgb)  # test_prediction has no sparse depth
#         else:
#             paths_d = sorted(glob.glob(glob_d))

#     if len(paths_d) == 0 and len(paths_rgb) == 0 and len(paths_gt) == 0:
#         raise (RuntimeError("Found 0 images under {}".format(glob_gt)))
#     if len(paths_d) == 0 and args.use_d:
#         raise (RuntimeError("Requested sparse depth but none was found"))
#     if len(paths_rgb) == 0 and args.use_rgb:
#         raise (RuntimeError("Requested rgb images but none was found"))
#     if len(paths_rgb) == 0 and args.use_g:
#         raise (RuntimeError("Requested gray images but no rgb was found"))
#     if len(paths_rgb) != len(paths_d) or len(paths_rgb) != len(paths_gt):
#         raise (RuntimeError("Produced different sizes for datasets"))

#     paths = {"rgb": paths_rgb, "d": paths_d, "gt": paths_gt}
#     return paths, transform


def rgb_read(filename):
    assert os.path.exists(filename), "file not found: {}".format(filename)
    img_file = Image.open(filename)
    # rgb_png = np.array(img_file, dtype=float) / 255.0 # scale pixels to the range [0,1]
    rgb_png = np.array(img_file, dtype='uint8')  # in the range [0,255]
    img_file.close()
    return rgb_png


def depth_read(filename):
    # loads depth map D from png file
    # and returns it as a numpy array,
    # for details see readme.txt
    assert os.path.exists(filename), "file not found: {}".format(filename)
    img_file = Image.open(filename)
    depth_png = np.array(img_file, dtype=int)
    img_file.close()
    # make sure we have a proper 16bit depth map here.. not 8bit!
    assert np.max(depth_png) > 255, \
        "np.max(depth_png)={}, path={}".format(np.max(depth_png),filename)

    depth = depth_png.astype(np.float) / 256.
    # depth[depth_png == 0] = -1.
    depth = np.expand_dims(depth, -1)
    return depth


oheight, owidth = 352, 1216


def drop_depth_measurements(depth, prob_keep):
    mask = np.random.binomial(1, prob_keep, depth.shape)
    depth *= mask
    return depth


def train_transform(rgb, sparse, rgb_near, args):
    # # s = np.random.uniform(1.0, 1.5) # random scaling
    # # angle = np.random.uniform(-5.0, 5.0) # random rotation degrees
    # do_flip = np.random.uniform(0.0, 1.0) < 0.5  # random horizontal flip

    # transform_geometric = transforms.Compose([
    #     # transforms.Rotate(angle),
    #     # transforms.Resize(s),
    #     transforms.BottomCrop((oheight, owidth)),
    #     transforms.HorizontalFlip(do_flip)
    # ])
    # if sparse is not None:
    #     sparse = transform_geometric(sparse)
    # # target = transform_geometric(target)
    # if rgb is not None:
    #     brightness = np.random.uniform(max(0, 1 - args.jitter),
    #                                    1 + args.jitter)
    #     contrast = np.random.uniform(max(0, 1 - args.jitter), 1 + args.jitter)
    #     saturation = np.random.uniform(max(0, 1 - args.jitter),
    #                                    1 + args.jitter)
    #     transform_rgb = transforms.Compose([
    #         transforms.ColorJitter(brightness, contrast, saturation, 0),
    #         transform_geometric
    #     ])
    #     rgb = transform_rgb(rgb)
    #     if rgb_near is not None:
    #         rgb_near = transform_rgb(rgb_near)
    # # sparse = drop_depth_measurements(sparse, 0.9)
    
    color_jitter = transforms.ColorJitter(0.4, 0.4, 0.4)
    
    s = np.random.uniform(1.0,1.5)
    depth_np = sparse/s
    angle = np.random.uniform(-5.0,5.0)
    do_flip = np.random.uniform(0.0,1.0)<0.5
   
    transform = transforms.Compose([
      transforms.Crop(130,10,220,1200),
      transforms.Rotate(angle),
      transforms.Resize(s),
      transforms.CenterCrop(size=(210,840)),
      transforms.HorizontalFlip(do_flip)
    ])
    rgb_np = transform(rgb)
    #print("transformed rgb1\n")
    rgb_np = color_jitter(rgb_np) # random color jittering
    rgb_np = np.asfarray(rgb_np, dtype='float') / 255	#Why do this??
    # Scipy affine_transform produced RuntimeError when the depth map was
    # given as a 'numpy.ndarray'
    if(rgb_near is not None):
      rgb_near = transform(rgb_near)
      #print("transformed rgb2\n")
      rgb_near = color_jitter(rgb_near)
      rgb_near = np.asfarray(rgb_near,dtype='float')/255
        
    
    depth_np = np.asfarray(sparse, dtype='float32') /255
    #print( depth_np.shape, rgb_np.shape)
    depth_np = transform(depth_np)
    #print("transformed depth\n")
    return rgb_np , depth_np, rgb_near


def val_transform(rgb, sparse, rgb_near, args):
    transform = transforms.Compose([
        transforms.Crop(130,10,220,1200),
        transforms.CenterCrop(size=(210,840))
    ])
    if rgb is not None:
        rgb = transform(rgb)
        rgb = np.asfarray(rgb,dtype='float')/255
    if sparse is not None:
        sparse = np.asfarray(sparse,dtype='float')/ 255
        sparse = transform(sparse)
    if rgb_near is not None:
        rgb_near = transform(rgb_near)
        rgb_near = np.asfarray(rgb,dtype='float')/255
    return rgb, sparse, rgb_near


def no_transform(rgb, sparse, rgb_near, args):
    return rgb, sparse, rgb_near


to_tensor = transforms.ToTensor()
to_float_tensor = lambda x: to_tensor(x).float()


def handle_gray(rgb, args):
    if rgb is None:
        return None, None
    if not args.use_g:
        return rgb, None
    else:
        img = np.array(Image.fromarray(rgb).convert('L'))
        img = np.expand_dims(img, -1)
        if not args.use_rgb:
            rgb_ret = None
        else:
            rgb_ret = rgb
        return rgb_ret, img


def get_rgb_near(imgs,index, args):
    
    max_frame_diff = 3
    candidates = [
        i - max_frame_diff for i in range(max_frame_diff * 2 + 1)
        if i - max_frame_diff != 0
    ]
    
    if args.loader == png_loader:
        head1,_ = os.path.split(imgs[index][0])
    
    if args.loader == h5_loader:
        head1,_ = os.path.split(imgs[index])
    
    while True:
        random_offset = choice(candidates)
        if( (index + random_offset) >= len(imgs) ):
            continue
            
        path_near = imgs[index + random_offset]
        
        if args.loader == png_loader:
            head2,_ = os.path.split(imgs[index][0])
    
        if args.loader == h5_loader:
            head2,_ = os.path.split(imgs[index])
        
        if os.path.exists(path_near) and head2 == head1:
            break
        

    return path_near

class KittiDepth(data.Dataset):

    """A data loader for the Kitti dataset
    """
    color_jitter = transforms.ColorJitter(0.4, 0.4, 0.4)
    def __init__(self,split,args):
        if(args.loader == png_loader):
            imgs = make_dataset_png(args.root)
        if(args.loader == h5_loader):
            imgs = make_dataset_h5(args.root)

#         if(type == val and len(imgs) > 3200):
#             np.random.shuffle(imgs)
#             imgs = imgs[:3200]

        assert len(imgs)>0, "Found 0 images in subfolders of: " + args.root + "\n"
        print("Found {} images in {} folder.".format(len(imgs), args.root))
        self.imgs = imgs

        if split == 'train':
            self.transform = train_transform
        elif split == 'val':
            self.transform = val_transform
        else:
            raise (RuntimeError("Invalid dataset type: " + type + "\n"
                                "Supported dataset types are: train, val"))
        self.loader = args.loader
        # assert (modality in self.modality_names), "Invalid modality type: " + modality + "\n" + \
        #                         "Supported dataset types are: " + ''.join(self.modality_names)
        self.modality = args.input
        self.sparsifier = args.sparsifier
        self.args = args
        self.split = split
        self.K = load_calib()
        self.threshold_translation = 0.1
        self.output_size = (210, 840)

    def create_sparse_depth(self, rgb, depth):
        if self.sparsifier is None:
            return depth
        else:
            mask_keep = self.sparsifier.dense_to_sparse(rgb, depth)
            sparse_depth = np.zeros(depth.shape)
            sparse_depth[mask_keep] = depth[mask_keep]
            return sparse_depth

    def create_rgbd(self, rgb, depth):
        sparse_depth = self.create_sparse_depth(rgb, depth)
        rgbd = np.append(rgb, np.expand_dims(sparse_depth, axis=2), axis=2) #Use the tensor version of expand_dims...
        return rgbd

    def __getraw__(self, index):
        # if self.loader == png_loader:

        # rgb = rgb_read(self.paths['rgb'][index]) if \
        #     (self.paths['rgb'][index] is not None and (self.args.use_rgb or self.args.use_g)) else None
        # sparse = depth_read(self.paths['d'][index]) if \
        #     (self.paths['d'][index] is not None and self.args.use_d) else None
        # # target = depth_read(self.paths['gt'][index]) if \
        # #     self.paths['gt'][index] is not None else None
        # rgb_near = get_rgb_near(self.paths['rgb'][index], self.args) if \
        #     self.split == 'train' and self.args.use_pose else None
        if self.loader == png_loader:
            rgb_path, depth_path = self.imgs[index]
            rgb, depth = self.loader(rgb_path, depth_path)
            rgb_near_path = get_rgb_near(self.imgs,index,self.args) if\
                self.split == 'train' and self.args.use_pose else None
            rgb_near,_ = self.loader(rgb_near_path,None)
        if self.loader == h5_loader:
            path = self.imgs[index]
            rgb, depth = self.loader(path)
            rgb_near_path = get_rgb_near(self.imgs, index,self.args) if\
                self.split == 'train' and self.args.use_pose else None
            rgb_near,_ = self.loader(rgb_near_path)
        return rgb, depth, rgb_near

    def __getitem__(self, index):
        rgb, sparse,  rgb_near = self.__getraw__(index)
        rgb, sparse,  rgb_near = self.transform(rgb, sparse, rgb_near, self.args)
        r_mat, t_vec = None, None
        if self.split == 'train' and self.args.use_pose:
            success, r_vec, t_vec = get_pose_pnp(rgb, rgb_near, sparse, self.K)
            # discard if translation is too small
            success = success and LA.norm(t_vec) > self.threshold_translation
            if success:
                r_mat, _ = cv2.Rodrigues(r_vec)
            else:
                # return the same image and no motion when PnP fails
                rgb_near = rgb
                t_vec = np.zeros((3, 1))
                r_mat = np.eye(3)
        #rgb, gray = handle_gray(rgb, self.args)
        gray = None
        
        candidates = {"rgb":rgb, "d":sparse, \
            "g":gray, "r_mat":r_mat, "t_vec":t_vec, "rgb_near":rgb_near,"rgbd":self.create_rgbd(rgb,sparse)}
        items = {
            key: to_float_tensor(val)
            for key, val in candidates.items() if val is not None
        }

        return items

    def __len__(self):
        return len(self.imgs)