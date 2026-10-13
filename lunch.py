"""월간 런치세트 게시물 만들기: 텔레그램 답장 해석 → 카드 이미지 2장 → 캡션.

카드는 2026-10 2주 캘린더와 같은 메뉴북 틀(웜화이트 바탕, 영문 라벨, 한글 제목, 빨간 로고)을 따른다.
"""
import difflib, os, re, subprocess, sys, unicodedata

ROOT = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1350
CANVAS = (250, 248, 245)
INK, MUTED, RED = (36, 31, 32), (128, 118, 119), (196, 32, 43)
ZONE = (70, 150, 1010, 1010)
DEFAULT_SET = "식전빵 + 파스타 + 커피"

# 메뉴판 설명을 줄인 한 줄 소개 (캡션용)
DESC = {
    "뚝배기해산물파스타": "바질 향 매콤한 토마토소스에 해산물을 듬뿍 담은 해장파스타",
    "알리오올리오": "진하고 구수한 마늘 향의 로마식 오일 파스타",
    "명란로제파스타": "감칠맛 나는 명란과 날치알이 어울린 로제 파스타",
    "봉골레": "모시조개와 바지락에 마늘 향을 더한 오일 파스타",
    "감베리올리오": "새우와 루꼴라를 마늘 오일로 요리한 파스타",
    "마레비안코": "신선한 해산물과 올리브오일로 맛을 낸 오일 파스타",
    "엔쵸비오일": "엔쵸비와 마늘로 맛을 낸 오일 파스타",
    "포모도로": "수제 토마토소스로 요리한 로마식 파스타",
    "아라비아따": "베이컨과 케이퍼가 들어간 매콤한 토마토 파스타",
    "마레로쏘": "해산물과 수제 토마토소스의 가장 대중적인 파스타",
    "돌문어토마토파스타": "국내산 돌문어로 요리한 매콤한 토마토 파스타",
    "감베로": "로제소스로 요리한 새우 파스타",
    "빠네": "바삭하게 구운 빵에 크림소스 파스타를 가득 채운 메뉴",
    "포르치니크림파스타": "포르치니 버섯 향이 풍부한 크림 파스타",
    "바실리코새우크림": "관자와 새우에 바질 향을 더한 크림 파스타",
    "안심크림파스타": "안심 스테이크와 버섯, 고르곤졸라 향 크림 파스타",
    "채끝칠리파스타": "그릴에 구운 등심과 칠리 토마토소스 파스타",
    "등심라구파스타": "그릴에 구운 등심과 로마식 볼로네제 파스타",
    "포르치니버섯리조또": "포르치니 버섯 향이 진한 리조또",
    "씨푸드라이스": "해산물을 풍부하게 즐기는 매콤한 라이스",
}
ALIASES = {"뚝빼기": "뚝배기", "앤초비": "엔쵸비", "엔초비": "엔쵸비", "알리오올리오파스타": "알리오올리오", "명란로제": "명란로제파스타"}


def norm(s):
    s = unicodedata.normalize("NFC", s).replace(" ", "").strip()
    for a, b in ALIASES.items():
        s = s.replace(a, b)
    return s


def photo_for(name):
    keys = {norm(os.path.splitext(f)[0]): f for f in os.listdir(os.path.join(ROOT, "photos"))}
    k = norm(name)
    if k in keys:
        return os.path.join(ROOT, "photos", keys[k])
    for key, f in keys.items():
        if k in key or key in k:
            return os.path.join(ROOT, "photos", f)
    close = difflib.get_close_matches(k, list(keys), n=1, cutoff=0.6)
    return os.path.join(ROOT, "photos", keys[close[0]]) if close else None


def parse(text):
    """'뚝배기 해산물 파스타 19,000원' 같은 줄들을 [(이름, 가격)]으로. '구성: …' 줄은 세트 구성으로."""
    items, setline = [], DEFAULT_SET
    for raw in text.splitlines():
        line = raw.strip().strip("-•·*").strip()
        if not line:
            continue
        m = re.match(r"^구성\s*[:：]\s*(.+)$", line)
        if m:
            setline = m.group(1).strip()
            continue
        m = re.match(r"^(.+?)[\s:：\-]*([0-9][0-9,\.]*)\s*(천원|원)?\s*$", line)
        if not m:
            raise ValueError(f"'{line}' 줄에서 메뉴 이름과 가격을 찾지 못했습니다")
        name, num, unit = m.group(1).strip(" :：-"), m.group(2).replace(",", ""), m.group(3)
        price = float(num)
        if unit == "천원" or price < 1000:
            price *= 1000
        items.append((name, int(round(price))))
    if not items:
        raise ValueError("메뉴가 한 줄도 없습니다")
    if len(items) > 3:
        raise ValueError("런치세트 메뉴는 3개까지 넣을 수 있습니다")
    return items, setline


def eul(word):
    """받침이 있으면 '을', 없으면 '를'."""
    ch = word.strip()[-1:]
    return "을" if ch and "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 else "를"


def won(n):
    return f"{n:,}원"


def caption(month, items, setline, footer, tags):
    lines = [f"{month}월 런치세트가 시작됐어요 🍝", "", f"{setline}{eul(setline)} 한 번에 즐기는 점심.", ""]
    for name, price in items:
        lines.append(f"{name} {won(price)}")
        d = DESC.get(norm(name))
        if d:
            lines.append(f"└ {d}")
    lines += ["", "평일 · 주말 모두 점심시간(11:30–15:00)에 즐기실 수 있어요.", "", footer, "", tags]
    return "\n".join(lines)


# ---------------------------------------------------------------- 이미지

def _pil():
    try:
        import PIL  # noqa: F401
    except ImportError:  # GitHub Actions 첫 사용 시에만 설치
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pillow==11.0.0"], check=True)
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
    return Image, ImageChops, ImageDraw, ImageFilter, ImageFont


def render(month, items, setline, out_dir, stem):
    Image, ImageChops, ImageDraw, ImageFilter, ImageFont = _pil()
    fp = lambda n: os.path.join(ROOT, "fonts", n)
    kr = lambda w, s: ImageFont.truetype(fp(f"Pretendard-{w}.otf"), s)

    def en(s, italic=True):
        f = ImageFont.truetype(fp("BodoniModa-Italic.ttf" if italic else "BodoniModa.ttf"), s)
        try:
            f.set_variation_by_axes([s if s <= 96 else 96, 400])  # opsz, wght
        except Exception:
            pass
        return f

    def to_canvas(im):
        w, h = im.size; px = []
        for x in range(0, w, max(1, w // 60)):
            px += [im.getpixel((x, 0)), im.getpixel((x, h - 1))]
        for y in range(0, h, max(1, h // 60)):
            px += [im.getpixel((0, y)), im.getpixel((w - 1, y))]
        bg = tuple(sorted(c[i] for c in px)[len(px) // 2] for i in range(3))
        out = []
        for i, ch in enumerate(im.split()):
            k = 255.0 / max(bg[i], 200); t = CANVAS[i] / 255.0
            out.append(ch.point(lambda v, k=k, t=t: int(min(255, v * k) * t)))
        return Image.merge("RGB", out)

    def cutout(path, box_w, box_h):
        im = to_canvas(Image.open(path).convert("RGB"))
        diff = ImageChops.difference(im, Image.new("RGB", im.size, CANVAS)).convert("L")
        diff = diff.filter(ImageFilter.GaussianBlur(3)).point(lambda v: 255 if v > 14 else 0)
        x0, y0, x1, y1 = diff.getbbox() or (0, 0, im.width, im.height)
        pad = int(max(x1 - x0, y1 - y0) * 0.05)
        im = im.crop((max(0, x0 - pad), max(0, y0 - pad), min(im.width, x1 + pad), min(im.height, y1 + pad)))
        r = min(box_w / im.width, box_h / im.height)
        im = im.resize((int(im.width * r), int(im.height * r)), Image.LANCZOS)
        f = 28
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rectangle((f, f, im.width - f, im.height - f), fill=255)
        return im, mask.filter(ImageFilter.GaussianBlur(f / 2))

    logo = to_canvas(Image.open(os.path.join(ROOT, "assets", "logo.png")).convert("RGB"))
    logo = logo.resize((logo.width // 2, logo.height // 2), Image.LANCZOS)

    def frame(label):
        c = Image.new("RGB", (W, H), CANVAS); d = ImageDraw.Draw(c)
        f = en(26, italic=False); text = label.upper()
        widths = [d.textlength(ch, font=f) for ch in text]; gap = 5
        x = (W - sum(widths) - gap * (len(text) - 1)) / 2
        for ch, w in zip(text, widths):
            d.text((x, 84), ch, font=f, fill=MUTED, anchor="lm"); x += w + gap
        c.paste(logo, ((W - logo.width) // 2, 1268 - logo.height // 2))
        return c, d

    def center(d, y, text, font, fill):
        d.text((W / 2, y), text, font=font, fill=fill, anchor="mm")

    paths = []
    # 1장: 메뉴 사진 + 제목 (피드 썸네일)
    c, d = frame("Lunch Set")
    photos = [p for p in (photo_for(n) for n, _ in items) if p]
    zw, zh = ZONE[2] - ZONE[0], ZONE[3] - ZONE[1]
    if photos:
        # 1개: 가득 / 2개: 위아래 엇갈림 / 3개: 위 2개 + 아래 1개
        if len(photos) == 1:
            boxes = [(0.5, 0.5, zw, zh)]
        elif len(photos) == 2:
            boxes = [(0.36, 0.25, 640, 420), (0.64, 0.75, 640, 420)]
        else:
            boxes = [(0.26, 0.27, 450, 400), (0.74, 0.27, 450, 400), (0.5, 0.75, 480, 400)]
        for p, (cx, cy, bw, bh) in zip(photos, boxes):
            im, mask = cutout(p, bw, bh)
            x = int(ZONE[0] + cx * zw - im.width / 2)
            y = int(ZONE[1] + cy * zh - im.height / 2)
            c.paste(im, (max(ZONE[0], min(x, ZONE[2] - im.width)), max(ZONE[1], min(y, ZONE[3] - im.height))), mask)
    else:
        center(d, (ZONE[1] + ZONE[3]) / 2, setline, kr("SemiBold", 52), INK)
    center(d, 1074, f"{month}월 런치세트", kr("SemiBold", 54), INK)
    center(d, 1140, "Bread · Pasta · Coffee" if setline == DEFAULT_SET else "Lunch of the month", en(34), MUTED)
    p1 = os.path.join(out_dir, f"{stem}_1.jpg"); c.save(p1, quality=92); paths.append(p1)

    # 2장: 메뉴와 가격
    c, d = frame("Lunch Set")
    rows = [("title", f"{month}월 런치세트"), ("set", setline), ("rule", None)]
    rows += [("item", (n, p)) for n, p in items] + [("note", "평일 · 주말 점심 11:30 – 15:00")]
    step = {"title": 110, "set": 70, "rule": 70, "item": 120, "note": 90}
    y = (150 + 1200) / 2 - sum(step[k] for k, _ in rows) / 2
    for kind, v in rows:
        cy = y + step[kind] / 2
        if kind == "title": center(d, cy, v, kr("Bold", 80), RED)
        elif kind == "set": center(d, cy, v, kr("Regular", 36), MUTED)
        elif kind == "rule": d.line((W / 2 - 30, cy, W / 2 + 30, cy), fill=RED, width=3)
        elif kind == "item":
            center(d, cy - 22, v[0], kr("SemiBold", 46), INK)
            center(d, cy + 34, won(v[1]), kr("Regular", 38), INK)
        elif kind == "note": center(d, cy + 10, v, kr("Regular", 30), MUTED)
        y += step[kind]
    p2 = os.path.join(out_dir, f"{stem}_2.jpg"); c.save(p2, quality=92); paths.append(p2)
    return paths


if __name__ == "__main__":  # 로컬 미리보기: python3 lunch.py 11 "뚝배기 해산물 파스타 19000\n알리오올리오 15000"
    items, setline = parse(sys.argv[2].replace("\\n", "\n"))
    print(render(int(sys.argv[1]), items, setline, sys.argv[3] if len(sys.argv) > 3 else ".", "lunch-test"))
