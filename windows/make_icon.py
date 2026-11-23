"""Рисует иконку приложения (app.ico, мультиразмерную) стандартным Pillow.

Запускается один раз при сборке; результат кладётся в windows/assets/app.ico.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "assets" / "app.ico"
S = 512                      # рабочее разрешение
MINT = (94, 234, 212)
TEAL = (45, 212, 191)
BLUE = (59, 130, 246)
INK = (7, 16, 24)


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def main() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # фон: скруглённый квадрат с диагональным градиентом mint -> blue
    pad = 16
    bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGBA", (S, S))
    gp = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S)          # непрерывный переход mint -> teal -> blue
            c = lerp(MINT, TEAL, t / 0.5) if t < 0.5 else lerp(TEAL, BLUE, (t - 0.5) / 0.5)
            gp[x, y] = (*c, 255)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [pad, pad, S - pad, S - pad], radius=108, fill=255)
    bg.paste(grad, (0, 0), mask)
    img = Image.alpha_composite(img, bg)
    d = ImageDraw.Draw(img)

    # внутренняя «рамка» для объёма
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=108,
                        outline=(255, 255, 255, 60), width=3)

    # символ «⇄»: две стрелки, белым
    w = 26                                     # толщина линий
    top_y, bot_y = 196, 316
    left_x, right_x = 148, 364

    # верхняя стрелка: слева направо, наконечник справа
    d.line([(left_x, top_y), (right_x - 56, top_y)], fill=INK, width=w)
    d.polygon([(right_x, top_y), (right_x - 74, top_y - 52),
               (right_x - 74, top_y + 52)], fill=INK)
    # нижняя стрелка: справа налево, наконечник слева
    d.line([(right_x, bot_y), (left_x + 56, bot_y)], fill=INK, width=w)
    d.polygon([(left_x, bot_y), (left_x + 74, bot_y - 52),
               (left_x + 74, bot_y + 52)], fill=INK)

    # три «узла» — намёк на сеть моделей
    for cx, cy, r in ((120, 118, 15), (392, 118, 15), (256, 408, 15)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 190))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"icon: {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
