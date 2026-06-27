import io
import re
import os
from PIL import Image, ImageDraw, ImageFont, ExifTags, ImageCms, ImageOps
import pillow_heif

# Register HEIF opener to support .heic and .heif files
pillow_heif.register_heif_opener()

def extract_exif(image):
    exif_data = {}
    info = image.getexif()
    if not info:
        return exif_data
        
    for tag, value in info.items():
        decoded = ExifTags.TAGS.get(tag, tag)
        exif_data[decoded] = value
        
    # Get standard Exif tags (sometimes nested under an ExifOffset tag)
    try:
        exif_info = info.get_ifd(ExifTags.IFD.Exif)
        for tag, value in exif_info.items():
            decoded = ExifTags.TAGS.get(tag, tag)
            exif_data[decoded] = value
    except AttributeError:
        pass    # Older pillow versions might not have get_ifd
        
    return exif_data

def apply_srgb_profile(img):
    """ICCプロファイルを使ってsRGBに変換する（色味の変化を防ぐ）"""
    original_info = img.info.copy()
    was_transformed = False
    try:
        icc_profile = img.info.get('icc_profile')
        if icc_profile:
            input_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
            srgb_profile = ImageCms.createProfile('sRGB')
            img = ImageCms.profileToProfile(
                img, input_profile, srgb_profile,
                outputMode='RGB'
            )
            was_transformed = True
    except Exception:
        pass

    if img.mode not in ('RGB', 'RGBA'):
        img = img.convert('RGB')
        was_transformed = True

    if was_transformed:
        # Preserve original metadata (Exif, etc.) as much as possible
        for k, v in original_info.items():
            if k not in ('icc_profile',):
                img.info[k] = v
    return img

def format_exif_text(exif_data):
    # Model
    camera = str(exif_data.get('Model', 'Unknown Camera')).strip('\0')
    
    # Focal Length
    focal_length_35mm = exif_data.get('FocalLengthIn35mmFilm')
    focal_length = exif_data.get('FocalLength')
    fl_text = ""
    if focal_length_35mm:
        try:
            fl_text = f"{int(focal_length_35mm)}mm "
        except:
            pass
    if not fl_text and focal_length:
        try:
            fl_val = float(focal_length)
            fl_text = f"{int(fl_val)}mm "
        except:
            pass
        
    # F Number
    f_num = exif_data.get('FNumber')
    f_text = ""
    if f_num:
        try:
            f_val = float(f_num)
            f_text = f"f/{f_val} "
        except:
            pass

    # Exposure Time (Shutter Speed)
    exposure = exif_data.get('ExposureTime')
    ss_text = ""
    if exposure:
        try:
            if float(exposure) < 1:
                ss_text = f"1/{int(1/float(exposure))}s "
            else:
                ss_text = f"{float(exposure)}s "
        except:
            pass

    # ISO
    iso = exif_data.get('ISOSpeedRatings')
    iso_text = f"ISO{iso}" if iso else ""
    
    # Lens Model (if available)
    lens = str(exif_data.get('LensModel', '')).replace('\0', '').strip()
    if lens:
        if 'iPhone' in camera:
            # Strip exact camera prefix if exists
            if lens.startswith(camera):
                lens = lens[len(camera):].strip('- ')
            # Remove redundant trailing specs e.g. " 5.7mm f/1.5"
            lens = re.sub(r'\s*\d+(\.\d+)?mm\s+f/\d+(\.\d+)?$', '', lens).strip()
            
    if fl_text: fl_text = fl_text.strip()
    if f_text: f_text = f_text.strip()
    if ss_text: ss_text = ss_text.strip()
    if iso_text: iso_text = iso_text.strip()
    
    # Date Time
    date_time = exif_data.get('DateTimeOriginal') or exif_data.get('DateTime', '')
    date_part = ""
    time_part = ""
    if date_time:
        date_time = str(date_time).replace('\0', '')
        if len(date_time) >= 19:
            try:
                date_str, time_str = date_time.split(" ", 1)
                date_part = date_str.replace(":", ".")
                time_part = time_str
            except:
                date_part = date_time
        else:
            date_part = date_time
            
    return camera, lens, fl_text, f_text, ss_text, iso_text, time_part, date_part

def process_image(image_bytes, camera_text, lens_text, fl_text, f_text, ss_text, iso_text, time_text, date_text, frame_ratio=0.03, banner_ratio=0.10, show_frame=True, bg_color='white', font_scale=1.0, font_family_main='arial', font_family_sub='arial', rotation_angle=0, flip_horizontal=False, exif_bytes_override=None, output_format='JPEG', show_banner=True, font_bytes_main=None, font_bytes_sub=None):
    # Open image from bytes
    img = Image.open(io.BytesIO(image_bytes))

    # Rotate image according to Exif Orientation (if exists)
    img = ImageOps.exif_transpose(img)

    # Prepare Exif bytes for saving. If override is provided, use it. Otherwise, try to get from original image and set Orientation to Normal.
    if exif_bytes_override is not None:
        exif_bytes = exif_bytes_override
    else:
        exif_bytes = None
        try:
            exif_obj = img.getexif()
            if exif_obj:
                exif_obj[274] = 1   # Set Orientation to Normal because we've already applied the rotation
                exif_bytes = exif_obj.tobytes()
        except Exception:
            pass

    # Apply sRGB profile if needed to prevent color shifts when saving in a different format or when the original image has an embedded profile.
    img = apply_srgb_profile(img)
    
    settings_parts = [t for t in [fl_text, f_text, ss_text, iso_text] if t]
    settings_text = "  ".join(settings_parts)
    
    # Combine date and time
    if date_text and time_text:
        dt_text = f"{date_text}   {time_text}"
    else:
        dt_text = f"{date_text}{time_text}".strip()
    
    if flip_horizontal:
        img = ImageOps.mirror(img)
    if rotation_angle:
        img = img.rotate(rotation_angle, expand=True)
        
    width, height = img.size
    
    # Calculate margins based on image size
    margin_top = margin_left = margin_right = int(width * frame_ratio) if show_frame else 0
    banner_height = int(width * banner_ratio) if show_banner else 0
    
    # If banner is shown, skip the bottom frame margin to prevent it from making the banner too thick
    if show_banner:
        margin_bottom = banner_height
    else:
        margin_bottom = int(width * frame_ratio) if show_frame else 0
    
    new_width = width + margin_left + margin_right
    new_height = height + margin_top + margin_bottom
    
    # Colors
    bg_rgb = (255, 255, 255) if bg_color == 'white' else (0, 0, 0)
    
    # Create new blank image
    new_img = Image.new('RGB', (new_width, new_height), bg_rgb)
    
    # Paste original image onto the canvas
    new_img.paste(img, (margin_left, margin_top))
    
    # Draw text if banner is visible
    if show_banner and banner_height > 0:
        draw = ImageDraw.Draw(new_img)
        
        # Theme colors for text
        text_main_rgb = (0, 0, 0) if bg_color == 'white' else (255, 255, 255)
        text_sub_rgb = (120, 120, 120) if bg_color == 'white' else (160, 160, 160)
        
        font_dir = os.path.join(os.path.dirname(__file__), "assets", "fonts")

        # Load main font path/bytes
        main_ttf = os.path.join(font_dir, "NotoSansJP-Bold.ttf")
        if font_family_main == 'roboto_bold':
            main_ttf = os.path.join(font_dir, "Roboto-Bold.ttf")
        elif font_family_main == 'uploaded_custom_font' and font_bytes_main is not None:
            main_ttf = io.BytesIO(font_bytes_main)
        elif font_family_main != 'noto_sans_bold':
            # Check if custom font path is provided or exists in fonts directory
            custom_path = os.path.join(font_dir, font_family_main)
            if os.path.exists(custom_path):
                main_ttf = custom_path
            elif os.path.exists(font_family_main):
                main_ttf = font_family_main

        # Load sub font path/bytes
        sub_ttf = os.path.join(font_dir, "NotoSansJP-Regular.ttf")
        if font_family_sub == 'roboto_regular':
            sub_ttf = os.path.join(font_dir, "Roboto-Regular.ttf")
        elif font_family_sub == 'uploaded_custom_font' and font_bytes_sub is not None:
            sub_ttf = io.BytesIO(font_bytes_sub)
        elif font_family_sub != 'noto_sans_regular':
            # Check if custom font path is provided or exists in fonts directory
            custom_path = os.path.join(font_dir, font_family_sub)
            if os.path.exists(custom_path):
                sub_ttf = custom_path
            elif os.path.exists(font_family_sub):
                sub_ttf = font_family_sub

        # Attempt to load the TrueType fonts
        try:
            font_main = ImageFont.truetype(main_ttf, size=int(width * 0.024 * font_scale))
            if font_family_sub == 'uploaded_custom_font' and font_bytes_sub is not None:
                # Re-create BytesIO for sub_ttf to ensure fresh read pointer
                sub_ttf = io.BytesIO(font_bytes_sub)
            font_sub = ImageFont.truetype(sub_ttf, size=int(width * 0.016 * font_scale))
        except IOError:
            # Fallback to system fonts if bundled fonts are missing
            try:
                font_main = ImageFont.truetype("arialbd.ttf", size=int(width * 0.024 * font_scale))
                font_sub = ImageFont.truetype("arial.ttf", size=int(width * 0.016 * font_scale))
            except IOError:
                font_main = ImageFont.load_default()
                font_sub = ImageFont.load_default()
            
        # Calculate text positions in the bottom margin area
        banner_center_y = margin_top + height + (margin_bottom / 2)
        gap = int(width * 0.015)  # gap between lines
        
        # Calculate vertical position based on text heights
        bbox_camera = draw.textbbox((0, 0), camera_text if camera_text else "A", font=font_main)
        main_h = bbox_camera[3] - bbox_camera[1]
        
        main_y = banner_center_y - main_h - int(gap / 2)
        sub_y = banner_center_y + int(gap / 2)
        
        # Text boundary edges (aligning with photo edges)
        padding_x_left = margin_left if show_frame else int(width * 0.03)
        padding_x_right = new_width - padding_x_left
        
        left_x = padding_x_left
        right_x = padding_x_right
        
        # Draw Camera Model (Left Top)
        if camera_text:
            draw.text((left_x, main_y), camera_text, fill=text_main_rgb, font=font_main)
        
        # Draw Lens Model (Left Bottom)
        if lens_text:
            draw.text((left_x, sub_y), lens_text, fill=text_sub_rgb, font=font_sub)
        
        # Draw Settings (Right Top)
        if settings_text:
            settings_bbox = draw.textbbox((0, 0), settings_text, font=font_main)
            settings_w = settings_bbox[2] - settings_bbox[0]
            draw.text((right_x - settings_w, main_y), settings_text, fill=text_main_rgb, font=font_main)
        
        # Draw Datetime (Right Bottom)
        if dt_text:
            dt_bbox = draw.textbbox((0, 0), dt_text, font=font_sub)
            dt_w = dt_bbox[2] - dt_bbox[0]
            draw.text((right_x - dt_w, sub_y), dt_text, fill=text_sub_rgb, font=font_sub)
        
    # Save to buffer
    output = io.BytesIO()
    fmt = output_format.upper() if output_format else 'JPEG'
    if fmt not in ('JPEG', 'PNG', 'TIFF', 'HEIF'):
        fmt = 'JPEG'
    save_kwargs = {'format': fmt}
    if fmt in ('JPEG', 'HEIF'):
        save_kwargs['quality'] = 95
    if exif_bytes:
        save_kwargs['exif'] = exif_bytes
    new_img.save(output, **save_kwargs)
    return output.getvalue()



