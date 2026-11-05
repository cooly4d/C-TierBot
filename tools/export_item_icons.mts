/**
 * Exports one square PNG icon per cosmetic item from a survev.de checkout into
 * assets/items/<type>.png, so /inventory and /market can show item previews
 * without the bot needing an SVG renderer or a Python copy of the item defs.
 *
 * The icon choice mirrors the survev.de locker (client/src/helpers.ts
 * getSvgFromGameType): outfits with their own loot art use it, generic-shirt
 * outfits get the composed in-game character (client/src/ui/loadoutIcon.ts),
 * and melee, emotes, particles, death effects, crate keys and crosshairs use
 * their single sprite.
 *
 * Run it from the survev.de checkout, which provides tsx, sharp and the defs:
 *   cd ../survev.de
 *   node_modules/.bin/tsx ../C-TierBot/tools/export_item_icons.mts
 * Options: --survev <dir> (default: cwd), --out <dir> (default: ../assets/items
 * next to this script), --size <px> (default: 128).
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

type Raster = { data: Buffer; width: number; height: number };
type Layer = { file: string; tint: number; x: number; y: number; scale: number };

function arg(name: string, fallback: string): string {
    const i = process.argv.indexOf(`--${name}`);
    return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const SURVEV = path.resolve(arg("survev", process.cwd()));
const OUT = path.resolve(arg("out", path.join(import.meta.dirname, "..", "assets", "items")));
const SIZE = Number(arg("size", "128"));
const IMG = path.join(SURVEV, "client", "public", "img");

if (!fs.existsSync(path.join(SURVEV, "shared", "defs", "register.ts"))) {
    console.error(`${SURVEV} is not a survev.de checkout (pass --survev <dir>).`);
    process.exit(1);
}

const sharp = createRequire(path.join(SURVEV, "client", "package.json"))("sharp");
const { GameObjectDefs } = await import(pathToFileURL(path.join(SURVEV, "shared", "defs", "register.ts")).href);

// Character layer scales and the idle fists pose, copied from loadoutIcon.ts
// (which takes them from Player.updateVisuals and IdlePoses.fists).
const BODY_SCALE = 0.25;
const HAND_SCALE = 0.175;
const BACKPACK_SCALE = 0.215;
const FRONT_SCALE = 0.27;
const BACKPACK_OFFSET = 10.25;
const HAND_L = { x: 14, y: -12.25 };
const HAND_R = { x: 14, y: 12.25 };
const PX_PER_UNIT = 6; // compose at this density, then downscale to SIZE
const GENERIC_SHIRTS = new Set(["loot-shirt-01.img", "loot-shirt-outfitBase.img"]);

/** "player-base-01.img" -> the source file in one of `dirs`, or null. */
function spriteFile(sprite: string | undefined, ...dirs: string[]): string | null {
    if (!sprite || !sprite.endsWith(".img")) return null;
    const stem = sprite.slice(0, -4);
    for (const dir of dirs) {
        for (const ext of [".svg", ".png"]) {
            const file = path.join(IMG, dir, stem + ext);
            if (fs.existsSync(file)) return file;
        }
    }
    return null;
}

async function nativeSize(file: string): Promise<{ width: number; height: number }> {
    const meta = await sharp(file).metadata();
    return { width: meta.width, height: meta.height };
}

/** Rasterizes `file` at `factor` x its native size, as raw RGBA. */
async function rasterize(file: string, factor: number): Promise<Raster> {
    const input = file.endsWith(".svg") ? sharp(file, { density: 72 * factor }) : sharp(file);
    let img = input.ensureAlpha();
    if (!file.endsWith(".svg")) {
        const { width, height } = await nativeSize(file);
        img = img.resize(Math.max(1, Math.round(width * factor)), Math.max(1, Math.round(height * factor)));
    }
    const { data, info } = await img.raw().toBuffer({ resolveWithObject: true });
    return { data, width: info.width, height: info.height };
}

/** PIXI-style tint: multiplies each colour channel by the tint's channel. */
function tint(raster: Raster, color: number): Raster {
    if (color === 0xffffff) return raster;
    const rgb = [(color >> 16) & 0xff, (color >> 8) & 0xff, color & 0xff];
    const data = Buffer.from(raster.data);
    for (let i = 0; i < data.length; i += 4) {
        for (let c = 0; c < 3; c++) data[i + c] = Math.round((data[i + c] * rgb[c]) / 255);
    }
    return { ...raster, data };
}

function rawInput(raster: Raster) {
    return { input: raster.data, raw: { width: raster.width, height: raster.height, channels: 4 } };
}

/** Centres `png` on a transparent square `margin` x its longest side, resized to SIZE. */
async function toIcon(png: Buffer, margin: number): Promise<Buffer> {
    const trimmed = await sharp(png).trim({ threshold: 0 }).png().toBuffer().catch(() => png);
    const { width, height } = await nativeSize(trimmed);
    const side = Math.ceil(Math.max(width, height) * margin);
    const left = Math.floor((side - width) / 2);
    const top = Math.floor((side - height) / 2);
    return sharp(trimmed)
        .extend({ top, bottom: side - height - top, left, right: side - width - left, background: { r: 0, g: 0, b: 0, alpha: 0 } })
        .png()
        .toBuffer()
        .then((square: Buffer) => sharp(square).resize(SIZE, SIZE).png({ compressionLevel: 9, palette: true, effort: 10 }).toBuffer());
}

/** The composed top-down character, like loadoutIcon.ts buildOutfitContent. Null if a sprite is missing. */
async function composeOutfit(def: any): Promise<Buffer | null> {
    const img = def.skinImg;
    const playerDirs = ["player", "player/accessories"];
    const base = spriteFile(img.baseSprite, ...playerDirs);
    if (!base) return null;

    const layers: Layer[] = [];
    const add = (sprite: string | undefined, color: number, x: number, y: number, scale: number) => {
        const file = spriteFile(sprite, ...playerDirs);
        if (file) layers.push({ file, tint: color, x, y, scale });
    };
    const fp = img.frontSpritePos ?? { x: 0, y: 0 };
    add(img.backpackSprite, img.backpackTint, -BACKPACK_OFFSET, 0, BACKPACK_SCALE);
    add(img.baseSprite, img.baseTint, 0, 0, BODY_SCALE);
    if (img.frontSprite && !img.aboveHand) add(img.frontSprite, 0xffffff, fp.x, fp.y, FRONT_SCALE);
    add(img.handSprite, img.handTint, HAND_L.x, HAND_L.y, HAND_SCALE);
    add(img.handSprite, img.handTint, HAND_R.x, HAND_R.y, HAND_SCALE);
    if (img.frontSprite && img.aboveHand) add(img.frontSprite, 0xffffff, fp.x, fp.y, FRONT_SCALE);

    // Rasterize every layer around its centre, then place them on one canvas.
    const placed = [];
    for (const layer of layers) {
        const raster = tint(await rasterize(layer.file, layer.scale * PX_PER_UNIT), layer.tint);
        placed.push({
            raster,
            left: layer.x * PX_PER_UNIT - raster.width / 2,
            top: layer.y * PX_PER_UNIT - raster.height / 2,
        });
    }
    const minX = Math.min(...placed.map((p) => p.left));
    const minY = Math.min(...placed.map((p) => p.top));
    const width = Math.ceil(Math.max(...placed.map((p) => p.left + p.raster.width)) - minX);
    const height = Math.ceil(Math.max(...placed.map((p) => p.top + p.raster.height)) - minY);

    const canvas = await sharp({ create: { width, height, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } } })
        .composite(placed.map((p) => ({
            ...rawInput(p.raster),
            left: Math.round(p.left - minX),
            top: Math.round(p.top - minY),
        })))
        .png()
        .toBuffer();
    // The player model faces +x; the locker shows it rotated a quarter turn clockwise.
    const rotated = await sharp(canvas).rotate(90).png().toBuffer();
    return toIcon(rotated, 1.2);
}

/** A single sprite, optionally tinted, mirrored and rotated (radians, like the locker's CSS transform). */
async function singleSprite(file: string, color = 0xffffff, rot = 0, mirror = false): Promise<Buffer> {
    const { width, height } = await nativeSize(file);
    const raster = tint(await rasterize(file, (SIZE * 2) / Math.max(width, height, 1)), color);
    let img = sharp(raster.data, { raw: { width: raster.width, height: raster.height, channels: 4 } });
    if (mirror) img = img.flop();
    let png = await img.png().toBuffer();
    if (rot) {
        png = await sharp(png).rotate((rot * 180) / Math.PI, { background: { r: 0, g: 0, b: 0, alpha: 0 } }).png().toBuffer();
    }
    return toIcon(png, 1.1);
}

/** Returns [kind, png] for a cosmetic item, or null when it has no locker icon. */
async function iconFor(def: any): Promise<[string, Buffer] | null> {
    switch (def.type) {
        case "outfit": {
            const loot = def.lootImg;
            if (!GENERIC_SHIRTS.has(loot.sprite)) {
                const file = spriteFile(loot.sprite, "loot");
                return file ? ["outfit", await singleSprite(file)] : null;
            }
            if (!def.ghillie && !def.obstacleType) {
                const composed = await composeOutfit(def);
                if (composed) return ["outfit", composed];
            }
            // Costumes and ghillie suits: the shirt in the skin's colour, as in the locker.
            const shirt = spriteFile(loot.sprite, "loot");
            return shirt ? ["outfit", await singleSprite(shirt, loot.tint)] : null;
        }
        case "melee": {
            const file = spriteFile(def.lootImg?.sprite, "loot");
            return file ? ["melee", await singleSprite(file, 0xffffff, def.lootImg.rot ?? 0, !!def.lootImg.mirror)] : null;
        }
        case "emote": {
            const file = spriteFile(def.texture, "emotes");
            return file ? ["emote", await singleSprite(file)] : null;
        }
        case "heal_effect":
        case "boost_effect": {
            const file = spriteFile(def.texture, "particles");
            return file ? [def.type, await singleSprite(file)] : null;
        }
        case "death_effect":
        case "crate_key": {
            const file = spriteFile(def.texture, "loot");
            return file ? [def.type, await singleSprite(file)] : null;
        }
        case "crosshair": {
            const file = spriteFile(def.texture, "crosshairs");
            return file ? ["crosshair", await singleSprite(file)] : null;
        }
        default:
            return null;
    }
}

fs.mkdirSync(OUT, { recursive: true });
for (const name of fs.readdirSync(OUT)) {
    if (name.endsWith(".png")) fs.rmSync(path.join(OUT, name)); // drop icons for removed items
}

const items: Record<string, { kind: string; name: string; rarity: number | null }> = {};
const missing: string[] = [];
let bytes = 0;
for (const type of GameObjectDefs.getAllTypes()) {
    if (!/^[A-Za-z0-9_-]+$/.test(type)) continue;
    const def = GameObjectDefs.typeToDefSafe(type);
    if (!def) continue;
    let result: [string, Buffer] | null = null;
    try {
        result = await iconFor(def);
    } catch (err) {
        console.warn(`${type}: ${(err as Error).message}`);
    }
    if (!result) {
        if (["outfit", "melee", "emote", "heal_effect", "boost_effect", "death_effect", "crate_key", "crosshair"].includes(def.type)) {
            missing.push(type);
        }
        continue;
    }
    const [kind, png] = result;
    fs.writeFileSync(path.join(OUT, `${type}.png`), png);
    items[type] = { kind, name: def.name ?? type, rarity: def.rarity ?? null };
    bytes += png.length;
}

// The currency icon the cards draw next to prices. The leading underscore keeps it
// apart from item types, which the bot only accepts when they start with a letter or digit.
const fries = path.join(IMG, "gui", "golden-fries.svg");
if (fs.existsSync(fries)) fs.writeFileSync(path.join(OUT, "_golden_fries.png"), await singleSprite(fries));

let commit = "unknown";
try {
    commit = execFileSync("git", ["-C", SURVEV, "rev-parse", "--short", "HEAD"], { encoding: "utf8" }).trim();
    // Uncommitted defs or art (e.g. unreleased skins) end up in the export too.
    const dirty = execFileSync("git", ["-C", SURVEV, "status", "--porcelain", "--", "shared/defs", "client/public/img"], {
        encoding: "utf8",
    });
    if (dirty.trim()) commit += "-dirty";
} catch {}
const manifest = { source: "survev.de", commit, size: SIZE, count: Object.keys(items).length, items };
fs.writeFileSync(path.join(OUT, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);

console.log(`Wrote ${manifest.count} icons (${(bytes / 1024 / 1024).toFixed(1)} MB) to ${OUT} from survev.de ${commit}.`);
if (missing.length) console.log(`No icon for ${missing.length} items: ${missing.join(", ")}`);
