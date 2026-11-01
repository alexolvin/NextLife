#!/usr/bin/env python3
"""Generate realistic, high-contrast visual product mock images with authentic graphics."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_TITLE = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf", 26)
FONT_SUBTITLE = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSans.ttf", 18)
FONT_BADGE = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf", 15)
FONT_TEXT = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSans.ttf", 16)
FONT_SMALL = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSans.ttf", 13)

def generate_thinkpad_images():
    base = Path("data/media/777")
    base_orig = base / "originals"
    base_proc = base / "processed"
    base_orig.mkdir(parents=True, exist_ok=True)
    base_proc.mkdir(parents=True, exist_ok=True)

    # 1. Front (Original)
    img = Image.new("RGB", (640, 640), (15, 23, 42))
    d = ImageDraw.Draw(img)
    d.rectangle([12, 12, 628, 628], outline=(59, 130, 246), width=3)

    # Laptop screen
    d.rounded_rectangle([100, 100, 540, 360], radius=10, fill=(30, 41, 59), outline=(100, 116, 139), width=3)
    d.rounded_rectangle([115, 115, 525, 345], radius=6, fill=(15, 23, 42))
    # Screen wallpaper gradient simulation
    d.rectangle([115, 260, 525, 345], fill=(37, 99, 235))
    d.text((150, 180), "ThinkPad P14s Gen 5", fill=(255, 255, 255), font=FONT_TITLE)
    d.text((150, 220), "Ryzen 7 8840HS • 32GB RAM", fill=(203, 213, 225), font=FONT_SUBTITLE)

    # Laptop base / keyboard
    d.polygon([(70, 370), (570, 370), (600, 490), (40, 490)], fill=(30, 41, 59), outline=(71, 85, 105))
    d.rectangle([120, 385, 520, 445], fill=(15, 23, 42)) # Keyboard area
    # Red TrackPoint!
    d.ellipse([315, 410, 325, 420], fill=(239, 68, 68))
    # Touchpad
    d.rounded_rectangle([270, 452, 370, 482], radius=4, fill=(51, 65, 85))

    # Badge
    d.rounded_rectangle([30, 30, 220, 68], radius=8, fill=(37, 99, 235))
    d.text((45, 40), "РОЛЬ: ORIGINAL", fill=(255, 255, 255), font=FONT_BADGE)

    # Bottom footer
    d.rectangle([20, 560, 620, 620], fill=(30, 41, 59))
    d.text((35, 570), "Bazoš.cz #223303840 | 1 192 EUR (29 800 CZK)", fill=(255, 255, 255), font=FONT_TEXT)
    d.text((35, 595), "640x640 • Реальное фото перед публикацией", fill=(148, 163, 184), font=FONT_SMALL)
    img.save(base_orig / "thinkpad_front.jpg", quality=95)

    # 2. Defect
    img_def = Image.new("RGB", (640, 640), (28, 15, 18))
    d2 = ImageDraw.Draw(img_def)
    d2.rectangle([12, 12, 628, 628], outline=(239, 68, 68), width=3)

    # Chassis surface
    d2.rounded_rectangle([60, 100, 580, 500], radius=16, fill=(39, 24, 28), outline=(120, 40, 50), width=2)
    d2.text((100, 140), "Нижняя крышка корпуса (макро)", fill=(255, 255, 255), font=FONT_TITLE)
    d2.text((100, 180), "Фиксация микропотертости краски", fill=(254, 202, 202), font=FONT_SUBTITLE)

    # Defect highlight circle
    d2.ellipse([340, 280, 480, 420], outline=(239, 68, 68), width=4)
    d2.line([(360, 350), (460, 350)], fill=(239, 68, 68), width=2) # scratch mark
    d2.text((310, 435), "[!] Потертость 2 мм", fill=(239, 68, 68), font=FONT_BADGE)

    # Badge
    d2.rounded_rectangle([30, 30, 210, 68], radius=8, fill=(239, 68, 68))
    d2.text((45, 40), "РОЛЬ: DEFECT", fill=(255, 255, 255), font=FONT_BADGE)

    # Bottom footer
    d2.rectangle([20, 560, 620, 620], fill=(45, 20, 25))
    d2.text((35, 570), "[VLM] VLM Verified: Дефект сохранён в комплекте", fill=(254, 202, 202), font=FONT_TEXT)
    d2.text((35, 595), "Честная фиксация для покупателя без сокрытия", fill=(248, 113, 113), font=FONT_SMALL)
    img_def.save(base_orig / "thinkpad_defect.jpg", quality=95)

    # 3. Processed / Publish (Grok AI)
    img_pub = Image.new("RGB", (640, 640), (248, 250, 252))
    d3 = ImageDraw.Draw(img_pub)
    d3.rectangle([12, 12, 628, 628], outline=(16, 185, 129), width=3)

    # Studio catalog view
    d3.rounded_rectangle([90, 110, 550, 390], radius=12, fill=(241, 245, 249), outline=(203, 213, 225), width=2)
    d3.text((130, 170), "Lenovo ThinkPad P14s Gen 5", fill=(15, 23, 42), font=FONT_TITLE)
    d3.text((130, 210), "Состояние: Отличное (Гарантия до 2027)", fill=(51, 65, 85), font=FONT_SUBTITLE)
    d3.text((130, 255), "[AI] Grok AI: Студийный белый фон", fill=(217, 119, 6), font=FONT_BADGE)
    d3.text((130, 285), "[VLM] VLM: Все маркеры аутентичности соблюдены", fill=(5, 150, 105), font=FONT_BADGE)

    # Badge
    d3.rounded_rectangle([30, 30, 220, 68], radius=8, fill=(16, 185, 129))
    d3.text((45, 40), "РОЛЬ: PUBLISH", fill=(255, 255, 255), font=FONT_BADGE)

    # Bottom footer
    d3.rectangle([20, 560, 620, 620], fill=(241, 245, 249))
    d3.text((35, 570), "Готово к публикации на Bazoš.cz и Sbazar.cz", fill=(15, 23, 42), font=FONT_TEXT)
    d3.text((35, 595), "Хеш пакета утверждения согласован", fill=(100, 116, 139), font=FONT_SMALL)
    img_pub.save(base_proc / "thinkpad_publish.jpg", quality=95)


def generate_iphone_images():
    base = Path("data/media/802")
    base_orig = base / "originals"
    base_proc = base / "processed"
    base_orig.mkdir(parents=True, exist_ok=True)
    base_proc.mkdir(parents=True, exist_ok=True)

    # 1. Front (Original)
    img = Image.new("RGB", (640, 640), (15, 23, 42))
    d = ImageDraw.Draw(img)
    d.rectangle([12, 12, 628, 628], outline=(14, 165, 233), width=3)

    # Phone silhouette
    d.rounded_rectangle([200, 90, 440, 520], radius=42, fill=(30, 58, 138), outline=(125, 211, 252), width=3)
    # Screen
    d.rounded_rectangle([212, 102, 428, 508], radius=34, fill=(15, 23, 42))
    # Notch
    d.rounded_rectangle([270, 102, 370, 126], radius=8, fill=(30, 58, 138))

    d.text((230, 220), "iPhone 13 Pro", fill=(255, 255, 255), font=FONT_TITLE)
    d.text((230, 260), "128 GB Sierra Blue", fill=(186, 230, 253), font=FONT_SUBTITLE)
    d.text((230, 300), "Батарея: 87%", fill=(16, 185, 129), font=FONT_BADGE)

    d.rounded_rectangle([30, 30, 220, 68], radius=8, fill=(37, 99, 235))
    d.text((45, 40), "РОЛЬ: ORIGINAL", fill=(255, 255, 255), font=FONT_BADGE)

    d.rectangle([20, 560, 620, 620], fill=(30, 41, 59))
    d.text((35, 570), "Bazoš ID: 223401923 | 450 EUR (11 250 CZK)", fill=(255, 255, 255), font=FONT_TEXT)
    d.text((35, 595), "Чистый экран, полный комплект с коробкой", fill=(148, 163, 184), font=FONT_SMALL)
    img.save(base_orig / "iphone_front.jpg", quality=95)

    # 2. Defect
    img_def = Image.new("RGB", (640, 640), (28, 15, 18))
    d2 = ImageDraw.Draw(img_def)
    d2.rectangle([12, 12, 628, 628], outline=(239, 68, 68), width=3)

    d2.rounded_rectangle([60, 100, 580, 500], radius=16, fill=(45, 20, 25), outline=(185, 28, 28), width=2)
    d2.text((100, 140), "Нижняя грань возле порта зарядки", fill=(255, 255, 255), font=FONT_TITLE)
    d2.text((100, 180), "Царапина 3 мм на стальной рамке", fill=(254, 202, 202), font=FONT_SUBTITLE)

    d2.ellipse([320, 270, 460, 410], outline=(239, 68, 68), width=4)
    d2.line([(350, 340), (430, 340)], fill=(239, 68, 68), width=3)
    d2.text((290, 430), "[!] Царапина на рамке 3 мм", fill=(239, 68, 68), font=FONT_BADGE)

    d2.rounded_rectangle([30, 30, 210, 68], radius=8, fill=(239, 68, 68))
    d2.text((45, 40), "РОЛЬ: DEFECT", fill=(255, 255, 255), font=FONT_BADGE)

    d2.rectangle([20, 560, 620, 620], fill=(45, 20, 25))
    d2.text((35, 570), "[VLM] VLM: Отсутствуют следы ретуши дефекта", fill=(254, 202, 202), font=FONT_TEXT)
    d2.text((35, 595), "Прозрачная фиксация состояния товара", fill=(248, 113, 113), font=FONT_SMALL)
    img_def.save(base_orig / "iphone_defect.jpg", quality=95)

    # 3. Publish
    img_pub = Image.new("RGB", (640, 640), (248, 250, 252))
    d3 = ImageDraw.Draw(img_pub)
    d3.rectangle([12, 12, 628, 628], outline=(16, 185, 129), width=3)

    d3.rounded_rectangle([90, 110, 550, 390], radius=12, fill=(241, 245, 249), outline=(203, 213, 225), width=2)
    d3.text((130, 170), "Apple iPhone 13 Pro 128GB", fill=(15, 23, 42), font=FONT_TITLE)
    d3.text((130, 210), "Цвет: Sierra Blue | iCloud чистый", fill=(51, 65, 85), font=FONT_SUBTITLE)
    d3.text((130, 255), "[AI] Grok AI: Оптимизация контраста под маркетплейсы", fill=(217, 119, 6), font=FONT_BADGE)
    d3.text((130, 285), "[VLM] VLM: Подтверждено соответствие оригиналу", fill=(5, 150, 105), font=FONT_BADGE)

    d3.rounded_rectangle([30, 30, 220, 68], radius=8, fill=(16, 185, 129))
    d3.text((45, 40), "РОЛЬ: PUBLISH", fill=(255, 255, 255), font=FONT_BADGE)

    d3.rectangle([20, 560, 620, 620], fill=(241, 245, 249))
    d3.text((35, 570), "Статус: APPROVED (Утверждено Владельцем)", fill=(15, 23, 42), font=FONT_TEXT)
    d3.text((35, 595), "Синхронизировано с Bazoš.cz и Aukro.cz", fill=(100, 116, 139), font=FONT_SMALL)
    img_pub.save(base_proc / "iphone_publish.jpg", quality=95)


def generate_helios_images():
    base = Path("data/media/348")
    base_orig = base / "originals"
    base_proc = base / "processed"
    base_orig.mkdir(parents=True, exist_ok=True)
    base_proc.mkdir(parents=True, exist_ok=True)

    # 1. Front (Original)
    img = Image.new("RGB", (640, 640), (15, 23, 42))
    d = ImageDraw.Draw(img)
    d.rectangle([12, 12, 628, 628], outline=(234, 179, 8), width=3)

    # Lens barrel circles
    d.ellipse([170, 120, 470, 420], fill=(30, 41, 59), outline=(203, 213, 225), width=4)
    d.ellipse([210, 160, 430, 380], fill=(15, 23, 42), outline=(100, 116, 139), width=2)
    # Glass reflection
    d.ellipse([250, 200, 390, 340], fill=(13, 148, 136))
    d.arc([260, 210, 380, 330], start=45, end=135, fill=(255, 255, 255), width=3)

    d.text((150, 440), "Helios 44-2 58mm f/2.0", fill=(255, 255, 255), font=FONT_TITLE)
    d.text((150, 480), "Байонет M42 • Легендарный крученый боке", fill=(253, 224, 71), font=FONT_SUBTITLE)

    d.rounded_rectangle([30, 30, 220, 68], radius=8, fill=(37, 99, 235))
    d.text((45, 40), "РОЛЬ: ORIGINAL", fill=(255, 255, 255), font=FONT_BADGE)

    d.rectangle([20, 560, 620, 620], fill=(30, 41, 59))
    d.text((35, 570), "Aukro ID: 7058291044 | 60 EUR (1 500 CZK)", fill=(255, 255, 255), font=FONT_TEXT)
    d.text((35, 595), "Диафрагма сухая, оптические элементы чистые", fill=(148, 163, 184), font=FONT_SMALL)
    img.save(base_orig / "helios_front.jpg", quality=95)

    # 2. Publish
    img_pub = Image.new("RGB", (640, 640), (248, 250, 252))
    d3 = ImageDraw.Draw(img_pub)
    d3.rectangle([12, 12, 628, 628], outline=(16, 185, 129), width=3)

    d3.rounded_rectangle([90, 110, 550, 390], radius=12, fill=(241, 245, 249), outline=(203, 213, 225), width=2)
    d3.text((130, 170), "Helios 44-2 58mm M42", fill=(15, 23, 42), font=FONT_TITLE)
    d3.text((130, 210), "Статус: Опубликовано на Aukro.cz и Bazoš.cz", fill=(51, 65, 85), font=FONT_SUBTITLE)
    d3.text((130, 255), "[AI] Каталожный кадр с идеальным светом", fill=(217, 119, 6), font=FONT_BADGE)
    d3.text((130, 285), "[VLM] VLM: Отсутствие царапин на линзах подтверждено", fill=(5, 150, 105), font=FONT_BADGE)

    d3.rounded_rectangle([30, 30, 220, 68], radius=8, fill=(16, 185, 129))
    d3.text((45, 40), "РОЛЬ: PUBLISH", fill=(255, 255, 255), font=FONT_BADGE)

    d3.rectangle([20, 560, 620, 620], fill=(241, 245, 249))
    d3.text((35, 570), "Активный листинг: 1 500 CZK | Заказов в доставке: 1", fill=(15, 23, 42), font=FONT_TEXT)
    d3.text((35, 595), "Проверенный селлер NextLife", fill=(100, 116, 139), font=FONT_SMALL)
    img_pub.save(base_proc / "helios_publish.jpg", quality=95)

if __name__ == "__main__":
    generate_thinkpad_images()
    generate_iphone_images()
    generate_helios_images()
    print("All authentic showcase image assets successfully generated with full Cyrillic support!")
