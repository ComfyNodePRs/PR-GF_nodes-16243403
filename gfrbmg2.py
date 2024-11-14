import torch, os
import torch.nn.functional as F
import folder_paths
from PIL import Image
from transformers import AutoModelForImageSegmentation
from torchvision import transforms
from torchvision.transforms.functional import normalize
import numpy as np
import cv2

device = "cuda" if torch.cuda.is_available() else "cpu"

# Добавляем путь к моделям ComfyUI
folder_paths.add_model_folder_path("rmbg_models", os.path.join(folder_paths.models_dir, "RMBG-2.0"))

def tensor2pil(image):
    return Image.fromarray(np.clip(255. * image.cpu().numpy().squeeze(), 0, 255).astype(np.uint8))

def pil2tensor(image):
    return torch.from_numpy(np.array(image).astype(np.float32) / 255.0).unsqueeze(0)

def resize_image(image):
    image = image.convert('RGB')
    model_input_size = (1024, 1024)
    image = image.resize(model_input_size, Image.BILINEAR)
    return image

class GFrbmg2:
    def __init__(self):
        self.model = None
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "invert_mask": ("BOOLEAN", {"default": False}),
                "postprocess_strength": ("FLOAT", {
                    "default": 0.0, 
                    "min": 0.0, 
                    "max": 20.0,
                    "step": 0.1
                }),
                "edge_enhancement": ("FLOAT", {
                    "default": 0.0, 
                    "min": 0.0, 
                    "max": 50.0,
                    "step": 0.1
                }),
                "blur_edges": ("FLOAT", {
                    "default": 0.0, 
                    "min": 0.0, 
                    "max": 50.0,
                    "step": 0.5
                }),
                "expand_mask": ("FLOAT", {
                    "default": 0.0, 
                    "min": -50.0,  # Сжатие маски
                    "max": 50.0,   # Расширение маски
                    "step": 0.1
                }),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "IMAGE")
    RETURN_NAMES = ("image_rgba", "mask", "image_black")
    FUNCTION = "remove_background"
    CATEGORY = "🐵 GorillaFrame/Image"
  
    def clean_mask(self, mask, strength, edge_enhancement, blur_edges, expand_mask):
        try:
            # Если все параметры равны 0, возвращаем исходную маску без обработки
            if strength == 0 and edge_enhancement == 0 and blur_edges == 0 and expand_mask == 0:
                return mask

            # Преобразование маски в формат OpenCV
            mask_np = np.array(mask)
            
            # Проверка валидности маски
            if mask_np is None or mask_np.size == 0:
                return mask

            # Расширение/сжатие маски если параметр не равен 0
            if expand_mask != 0:
                try:
                    kernel_size = int(5 * abs(expand_mask))
                    kernel_size = max(1, kernel_size)
                    if kernel_size % 2 == 0:
                        kernel_size += 1
                    kernel = np.ones((kernel_size, kernel_size), np.uint8)
                    
                    if expand_mask > 0:
                        mask_np = cv2.dilate(mask_np, kernel, iterations=1)
                    else:
                        mask_np = cv2.erode(mask_np, kernel, iterations=1)
                except:
                    pass

            # Если есть улучшение краев
            if edge_enhancement > 0:
                try:
                    lower_threshold = int(100 * edge_enhancement)
                    upper_threshold = int(200 * edge_enhancement)
                    edges = cv2.Canny(mask_np, lower_threshold, upper_threshold)

                    # Размытие краев если параметр больше 0
                    if blur_edges > 0:
                        try:
                            blur_size = int(5 * blur_edges)
                            if blur_size % 2 == 0:
                                blur_size += 1
                            edges = cv2.GaussianBlur(edges, (blur_size, blur_size), 0)
                        except:
                            pass

                    mask_np = cv2.bitwise_or(mask_np, edges)
                except:
                    pass

            # Постобработка и сглаживание применяются в конце
            if strength > 0:
                try:
                    kernel_size = int(5 * strength)
                    kernel_size = max(1, kernel_size)
                    if kernel_size % 2 == 0:
                        kernel_size += 1

                    kernel = np.ones((kernel_size, kernel_size), np.uint8)
                    iterations = int(strength)

                    # Сохраняем оригинальную маску для последующего применения
                    original_mask = mask_np.copy()

                    # Сначала применяем медианный фильтр для сглаживания
                    mask_smoothed = cv2.medianBlur(mask_np, kernel_size)

                    # Затем применяем морфологические операции только внутри маски
                    mask_dilated = cv2.dilate(mask_smoothed, kernel, iterations=iterations)
                    mask_eroded = cv2.erode(mask_dilated, kernel, iterations=iterations)

                    # Применяем результат только внутри оригинальной маски
                    mask_np = cv2.bitwise_and(mask_eroded, original_mask)

                except:
                    pass

            # Преобразование обратно в формат PIL
            try:
                cleaned_mask = Image.fromarray(mask_np)
                return cleaned_mask
            except:
                return mask

        except:
            return mask

    def remove_background(self, image, invert_mask, postprocess_strength, edge_enhancement, blur_edges, expand_mask):
        if self.model is None:
            self.model = AutoModelForImageSegmentation.from_pretrained(
                os.path.join(folder_paths.models_dir, "RMBG-2.0"),
                trust_remote_code=True,
                local_files_only=True
            )
            self.model.to(device)
            self.model.eval()

        processed_images = []
        processed_masks = []
        processed_blacks = []

        for img in image:
            orig_image = tensor2pil(img)
            w,h = orig_image.size
            image = resize_image(orig_image)
            im_np = np.array(image)
            im_tensor = torch.tensor(im_np, dtype=torch.float32).permute(2,0,1)
            im_tensor = torch.unsqueeze(im_tensor,0)
            im_tensor = torch.divide(im_tensor,255.0)
            im_tensor = normalize(im_tensor,[0.485, 0.456, 0.406],[0.229, 0.224, 0.225])
            if torch.cuda.is_available():
                im_tensor=im_tensor.cuda()

            with torch.no_grad():
                result = self.model(im_tensor)[-1].sigmoid().cpu()
            
            result = result[0].squeeze()
            result = F.interpolate(result.unsqueeze(0).unsqueeze(0), size=(h,w), mode='bilinear').squeeze()
            
            mask_pil = tensor2pil(result)
            # Чистка маски с учетом всех параметров
            mask_pil = self.clean_mask(mask_pil, postprocess_strength, edge_enhancement, blur_edges, expand_mask)
            
            # Инверсия маски после всех операций обработки
            if invert_mask:
                mask_np = np.array(mask_pil)
                mask_np = 255 - mask_np  # Инвертируем значения
                mask_pil = Image.fromarray(mask_np)
            
            # RGBA image
            rgba_image = orig_image.copy()
            rgba_image.putalpha(mask_pil)
            
            # Black background image
            black_image = Image.new('RGB', orig_image.size, (0, 0, 0))
            black_image.paste(orig_image, mask=mask_pil)

            processed_images.append(pil2tensor(rgba_image))
            processed_masks.append(pil2tensor(mask_pil))
            processed_blacks.append(pil2tensor(black_image))

        new_images = torch.cat(processed_images, dim=0)
        new_masks = torch.cat(processed_masks, dim=0)
        new_blacks = torch.cat(processed_blacks, dim=0)

        return new_images, new_masks, new_blacks