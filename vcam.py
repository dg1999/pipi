import util
import vcammat

import OpenEXR
import numpy as np

import os
import json
from os.path import join, exists


class Dataset(object):
    def __init__(self, dataset_name):
        self.name = dataset_name
        self.base_dir = join(os.getenv('VCAM_ROOT'), 'data', dataset_name)
        self.data_dir = join(self.base_dir, 'data')
        self.projpoints_dir = join(self.data_dir, 'projpoints')
        self.pid_dir = join(self.data_dir, 'pids')

        self.results_dir = join(self.base_dir, 'results')

        # load image_info
        imginfo_fn = join(self.data_dir, 'image_info.json')
        with open(imginfo_fn) as f:
            self.image_info = json.load(f)
        self.size = len(self.image_info)

        # set whether this is a 3d dataset or not
        self.is3d = False if 'projpoints' in self.image_info[0]['filename'] else True

        # load a global mask if exists
        self.global_mask = None
        global_mask_fn = join(self.projpoints_dir, 'mask.png')
        if exists(global_mask_fn):
            self.global_mask = util.imread(global_mask_fn) > 0
            if self.global_mask.ndim > 2:
                self.global_mask = self.global_mask[:,:,0]

        # get dimensions
        im3d, _, _, _ = self.load_rec(self.image_info[0])
        self.dims = im3d.shape

        self.normals, self.coords3d = self._load_normals_coords()

    def load_rec(self, rec):
        """
        Load image and related data given an image_info record.

        Returns im3d, mask3d, coords2d, im2d.
        For a 2d dataset, im3d == im2d,
                        mask3d == self.global_mask
                      coords2d == None
        """
        if self.is3d:
            fn = join(self.projpoints_dir, rec['pmvsid'] + '_0000.exr')
            im3d, mask3d, coords2d = imread_projpoints(fn)
            im2d = util.imread(join(self.data_dir, rec['filename']))
        else:
            im3d_fn = join(self.data_dir, rec['filename'])
            im3d = util.imread(im3d_fn)

            # assumes for now that 2d datasets no per-image masks
            if self.global_mask is not None:
                mask3d = self.global_mask
            else:
                mask3d = np.ones(im3d[:,:,0].shape, dtype=bool)
            coords2d = None
            im2d = im3d
        return im3d, mask3d, coords2d, im2d

    def load_pid(self, i, j):
        """
        Load intensity values for a single pixel.

        pid[valid] is a list of all values
        np.where(valid) is the corresponding list of image indices
        """
        data = vcammat.loadMat(join(self.pid_dir, '%04d/%04d.vcammat' % (i,j)))
        pid, valid = data[:,0], data[:,1] > 0
        return pid, valid

    def _load_normals_coords(self):
        normals = None
        normals_fn = join(self.projpoints_dir, 'pntnormals.vcammat')
        if not exists(normals_fn):
            print "pntnormals.vcammat does not exist."
        else:
            normals = vcammat.loadMat(join(self.projpoints_dir, normals_fn))
            # make sure they're 1-length
        #mask = np.logical_not(np.all(normals == -1, axis=2))
        #n = normals[mask,:]
        #norms = np.sqrt(np.sum(n**2, axis=1))
        #normals[mask,:] = util.div_nonz(normals[mask,:], np.tile(norms[:,np.newaxis], (1,3)))

            nshape = normals.shape
            n2enu_fn = join(self.data_dir, 'native2enu.txt')
            try:  # BAFP
                n2enu = np.loadtxt(n2enu_fn)
                if n2enu.shape == (4,4):
                    n2enu = n2enu[:3,:3]
            except IOError:
                print "Couldn't find native2enu file. Using identity."
                n2enu = np.eye(3)

            n = np.reshape(normals, (-1, 3))
            nt = np.dot(n, n2enu.T)
            normals = np.reshape(nt, nshape)

        coords = None
        coords_fn = join(self.projpoints_dir, 'coords.vcammat')
        if exists(coords_fn):
            coords = vcammat.loadMat(coords_fn)
        else:
            if self.is3d:
                print "coords.vcammat does not exist."
            else:
                print "inventing coords for 2d dataset"
                # make up arbitrary coordinates for
                # 2d datasets without coords
                x,z = np.meshgrid(xrange(self.dims[0]-1,-1,-1), xrange(self.dims[1]-1,-1,-1))
                y = np.zeros_like(x)
                coords = np.dstack((x,y,z))

        return normals, coords


def setup_dirs(dataset):
    # returns base_dir, data_dir, results_dir, image_info, is3d
    base_dir = join(os.getenv('VCAM_ROOT'), 'data', dataset)
    data_dir = join(base_dir, 'data')
    results_dir = join(base_dir, 'results')

    image_info = None
    is3d = None
    imginfo_fn = join(data_dir, 'image_info.json')
    if exists(imginfo_fn):
        with open(imginfo_fn) as f:
            image_info = json.load(f)
        is3d = False if 'projpoints' in image_info[0]['filename'] else True
    return base_dir, data_dir, results_dir, image_info, is3d



def load_from_rec(dataset, rec, is3d=True):
    # returns im3d, mask3d, coords2d, im2d
    base_dir = '/home/swehrwein/vcam/data/'
    data_dir = join(base_dir, dataset, 'data')

    im2d_fn = join(data_dir, rec['filename'])
    im2d = util.imread(im2d_fn)

    if is3d:
        im3d_fn = join(data_dir, 'projpoints', rec['pmvsid'] + '_0000.exr')
        im3d, mask3d, coords2d = imread_projpoints(im3d_fn)

    return im3d, mask3d, coords2d, im2d


def imread_projpoints(filename):
    # returns img, valid, coords
    exrimage = OpenEXR.InputFile(filename)

    dw = exrimage.header()['dataWindow']
    (width, height) = (dw.max.x - dw.min.x + 1, dw.max.y - dw.min.y + 1)

    def fromstr(s, datatype):
        mat = np.fromstring(s, dtype=datatype)
        mat = mat.reshape(height,width)
        return mat

    npy_dt = {'HALF':   np.float16,
              'FLOAT':  np.float32}

    channels = exrimage.header()['channels']

    # image data - RGB or Y
    if all([clr in channels for clr in 'RGB']):
        ch = 'RGB'
    elif 'Y' in channels:
        ch = 'Y'
    im_t = str(channels[ch[0]].type)
    img_data = [fromstr(s, npy_dt[im_t]) for s in exrimage.channels(ch)]
    img = np.dstack(img_data)

    # valid mask
    valid = None
    if 'valid' in channels:
        valid_t = str(channels['valid'].type)
        valid = fromstr(exrimage.channel('valid'), npy_dt[valid_t])

    # coordinate data
    coords = None
    coord_ch = ['point_image_coordinate_%s' % c for c in 'xy']
    if all([c in channels for c in coord_ch]):
        coord_t = str(channels[coord_ch[0]].type)
        coord_data = [fromstr(s, npy_dt[coord_t])
                      for s in exrimage.channels(coord_ch)]  # , exr_dt[coord_t])]
        coords = np.dstack(coord_data)

    return img, valid > 0, coords
