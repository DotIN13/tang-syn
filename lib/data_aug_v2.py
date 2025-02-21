import random
from random import choices
import logging

import numpy as np
import cv2
import torch
import torchvision.transforms.v2 as transforms
from torchvision.transforms.v2.functional import InterpolationMode, rgb_to_grayscale, pad
from kornia import morphology

# 0: InterpolationMode.NEAREST,
# 2: InterpolationMode.BILINEAR,
# 3: InterpolationMode.BICUBIC,
# 4: InterpolationMode.BOX,
# 5: InterpolationMode.HAMMING,
# 1: InterpolationMode.LANCZOS,

logger = logging.getLogger(__name__)


def create_connected_kernel(min_ones=2, max_ones=3):
    """Create a random 3x3 kernel with a random number of ones connected to each other"""
    kernel = np.zeros((3, 3))
    num_ones = np.random.randint(min_ones, max_ones+1)

    # Possible moves: up, down, left, right
    moves = [(-1, 0), (1, 0), (0, -1), (0, 1)]

    # Start at a random position
    cur_pos = (np.random.randint(3), np.random.randint(3))

    for _ in range(num_ones):
        kernel[cur_pos] = 1

        # Calculate the next position
        valid_moves = [move for move in moves if (
            0 <= cur_pos[0] + move[0] < 3) and (0 <= cur_pos[1] + move[1] < 3)]
        if valid_moves:
            cur_move = choices(valid_moves, k=1)[0]
            cur_pos = (cur_pos[0] + cur_move[0], cur_pos[1] + cur_move[1])
        else:
            break

    return kernel


# Ref for varying kernel size: Self-supervised Vision Transformers with Data Augmentation Strategies Using Morphological Operations for Writer Retrieval
class Dilation(torch.nn.Module):

    def __init__(self, device="cpu"):
        super().__init__()
        self.kernel = torch.from_numpy(create_connected_kernel())
        if device:
            self.kernel.to(device)

    def forward(self, img):

        if len(img.shape) == 3:
            img = img.unsqueeze(0)
            return morphology.dilation(img, self.kernel).squeeze()
        else:
            return morphology.dilation(img, self.kernel)

    def __repr__(self):
        return self.__class__.__name__ + f'(kernel={self.kernel})'


class Erosion(torch.nn.Module):

    def __init__(self, device="cpu"):
        super().__init__()
        self.kernel = torch.from_numpy(create_connected_kernel())
        if device:
            self.kernel.to(device)

    def forward(self, img):

        if len(img.shape) == 3:
            img = img.unsqueeze(0)
            return morphology.erosion(img, self.kernel).squeeze()
        else:
            return morphology.erosion(img, self.kernel)

    def __repr__(self):
        return self.__class__.__name__ + f'(kernel={self.kernel})'


class Underline(torch.nn.Module):

    def __init__(self, threshold=0.5):
        super().__init__()
        self.threshold = threshold

    def forward(self, img_tensor):
        if len(img_tensor.shape) == 3:  # If the input tensor has 3 dimensions (C, H, W)
            # Add an extra dimension to make it (1, C, H, W)
            img_tensor = img_tensor.unsqueeze(0)
            was_3d = True
        else:
            was_3d = False

        batch_size, _, height, width = img_tensor.shape
        # print(img_tensor.shape)
        grayscale = rgb_to_grayscale(img_tensor, num_output_channels=1)

        underline_mask = torch.zeros_like(img_tensor)

        for b in range(batch_size):
            black_pixels = torch.where(grayscale[b] < self.threshold)
            # print(black_pixels[0].shape, black_pixels[1].shape, black_pixels[2].shape)

            if len(black_pixels[0]) > 0:  # Check if there are black pixels
                y1 = min(int(torch.max(black_pixels[1])), height - 1)
                x0 = max(int(torch.min(black_pixels[2])), 0)
                x1 = min(int(torch.max(black_pixels[2])), width - 1)
                # print(y1, x0, x1)

                if abs(x0 - x1) < width * 0.2:
                    continue

                for x in range(x0, x1):
                    for y in range(y1, max(y1 - 3, 0), -1):
                        underline_mask[b, :, y, x] = 1.0

        img_tensor = img_tensor * (1 - underline_mask) + underline_mask * 0.0

        if was_3d:  # If the input tensor was originally 3D
            img_tensor = img_tensor.squeeze(0)  # Remove the extra dimension

        return img_tensor


class RandomInkSpots(torch.nn.Module):

    def __init__(self, ink_spots_num=2, ink_spot_size=3):
        super().__init__()
        self.ink_spots_num = ink_spots_num
        self.ink_spot_size = ink_spot_size

    def forward(self, img):
        if len(img.shape) == 4:  # if image is BCHW
            batch_size = img.shape[0]
            for i in range(batch_size):
                img[i] = self.ink_spot_transform(img[i])
        else:  # if image is CHW
            img = self.ink_spot_transform(img)

        return img

    def ink_spot_transform(self, img):
        # Convert PyTorch tensor to numpy array
        img = img.permute(1, 2, 0).numpy()

        # Create a new numpy array with the correct memory layout
        img = np.ascontiguousarray(img)

        # The input must be a torch tensor of float dtype
        img = (img * 255).astype(np.uint8)

        spot_num = max(1, int(np.random.normal(self.ink_spots_num, 3)))

        # Random color for each ink spot
        colors = [(random.randint(0, 255), random.randint(
            0, 255), random.randint(0, 255)) for _ in range(3)]
        colors.append((0, 0, 0))

        for _ in range(spot_num):
            # Random position for the center of the ink spot
            center_x = random.uniform(0, img.shape[1])
            center_y = random.uniform(0, img.shape[0])

            # Generate some random points around the center
            points = []
            for _ in range(5):
                size = random.uniform(0, self.ink_spot_size)
                point = (random.uniform(center_x - size, center_x + size),
                         random.uniform(center_y - size, center_y + size))
                points.append(point)

            # Convert points to an np.int32 array
            points = np.array(points, np.int32).reshape((-1, 1, 2))

            # Draw the ink spot using OpenCV
            cv2.fillPoly(img, [points], random.choice(colors))

        # Convert numpy array back to PyTorch tensor
        img = (img / 255.0)
        img = torch.from_numpy(img).permute(2, 0, 1)

        return img


class ResizePad(torch.nn.Module):
    """Resize and pad the image to a fixed size"""

    def __init__(self, width=1200):
        super().__init__()
        self.width = width

    def forward(self, img):
        if img.shape[-1] < self.width:
            return pad(img, (0, 0, self.width - img.shape[-1], 0), padding_mode='edge')

        return img


class KeepOriginal(torch.nn.Module):

    def __init__(self):
        super().__init__()

    def forward(self, img):
        return img


# def build_data_aug(size, mode="train", resnet=False, resizepad=False, device="cpu"):

#     if mode == 'train':
#         return transforms.Compose([
#             transforms.ToImageTensor(),
#             transforms.ConvertImageDtype(dtype=torch.float32),
#             transforms.RandomChoice([
#                 Erosion(device=device),
#                 Dilation(device=device),
#                 KeepOriginal()
#             ], p=[.33, .33, .34]),
#             transforms.RandomChoice([
#                 Underline(),
#                 RandomInkSpots(),
#                 KeepOriginal()
#             ], p=[.1, .2, .7]),
#             transforms.RandomChoice([
#                 transforms.RandomRotation(degrees=(-3, 3),
#                                           expand=True,
#                                           fill=(1, 1, 1)),
#                 transforms.GaussianBlur(3),
#                 transforms.Resize(
#                     size // 2, InterpolationMode.BILINEAR, antialias=True),
#                 KeepOriginal(),
#             ], p=[.2, .2, .2, .4]),
#             transforms.ToImagePIL()
#         ])

#     return None

def build_data_aug(height=64, width=1024, mode="train", resizepad=False, device="cpu"):

    if mode == "eval" and not resizepad:
        return None

    aug_steps = [
        transforms.ToImage(),
        transforms.ToDtype(dtype=torch.float32, scale=True),
    ]

    if mode == 'train':
        aug_steps.append(
            transforms.RandomChoice([
                Erosion(device=device),
                Dilation(device=device),
                Underline(),
                RandomInkSpots(),
                transforms.RandomRotation(degrees=(-3, 3),
                                          expand=True,
                                          fill=(1, 1, 1)),
                transforms.GaussianBlur(3),
                transforms.Resize(
                    height // 2, InterpolationMode.BILINEAR, antialias=True),
                KeepOriginal(),
            ])
        )

    if resizepad:
        aug_steps.append(ResizePad(width=width))

    aug_steps.append(transforms.ToPILImage())

    return transforms.Compose(aug_steps)
