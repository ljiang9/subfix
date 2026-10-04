#!/usr/bin/env python3
"""subfix - SRT 字幕工具箱：移位、变速、合并、切分、清洗、翻译、检查。

纯 Python 标准库，零第三方依赖。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# 时间
# ---------------------------------------------------------------------------

TS_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
ARROW_RE = re.compile(
    r"(\d+:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d+:\d{2}:\d{2}[,.]\d{1,3})"
)


def parse_ts(s: str) -> int:
    """'HH:MM:SS,mmm' -> 毫秒整数。"""
    m = TS_RE.fullmatch(s.strip())
    if not m:
        raise ValueError(f"无法解析时间戳: {s!r}")
    h, mi, se, ms = m.groups()
    ms = (ms + "000")[:3]
    return (int(h) * 3600 + int(mi) * 60 + int(se)) * 1000 + int(ms)


def format_ts(ms: int) -> str:
    """毫秒整数 -> 'HH:MM:SS,mmm'（负数钳制为 0）。"""
    ms = max(0, int(round(ms)))
    h, ms = divmod(ms, 3600000)
    mi, ms = divmod(ms, 60000)
    se, ms = divmod(ms, 1000)
    return f"{h:02d}:{mi:02d}:{se:02d},{ms:03d}"


def format_dur(ms: int) -> str:
    s = ms / 1000
    return f"{s:.1f}s" if s < 60 else f"{s/60:.1f}min"


# ---------------------------------------------------------------------------
# 数据模型与解析
# ---------------------------------------------------------------------------

class Cue:
    __slots__ = ("index", "start", "end", "lines")

    def __init__(self, index: int, start: int, end: int, lines: list[str]):
        self.index = index
        self.start = start
        self.end = end
        self.lines = lines

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def duration(self) -> int:
        return self.end - self.start

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Cue({self.index}, {format_ts(self.start)}-->{format_ts(self.end)})"


def parse_srt(raw: str) -> list[Cue]:
    """解析 SRT 文本。容忍：BOM、\\r\\n、末尾缺空行、块内多余空行。"""
    text = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    # 按空行切块；块内部允许空行被吞掉（逐行状态机更稳）
    cues: list[Cue] = []
    idx, start, end, lines = None, None, None, []
    seen_timing = False

    def flush():
        nonlocal idx, start, end, lines, seen_timing
        if seen_timing and start is not None and end is not None:
            cues.append(Cue(idx if idx is not None else len(cues) + 1,
                            start, end, lines))
        idx, start, end, lines = None, None, None, []
        seen_timing = False

    for raw_line in text.split("\n"):
        line = raw_line.strip("\u200b")  # 去零宽字符
        if not line.strip():
            if seen_timing:
                flush()
            continue
        if not seen_timing:
            if line.strip().isdigit():
                idx = int(line.strip())
                continue
            m = ARROW_RE.search(line)
            if m:
                start, end = parse_ts(m.group(1)), parse_ts(m.group(2))
                seen_timing = True
                continue
            # 既不是编号也不是时间轴：孤立文本行，忽略
            continue
        lines.append(raw_line.rstrip())
    flush()
    return cues


def format_srt(cues: list[Cue], bom: bool = False) -> str:
    """把字幕列表序列化为 SRT 文本（自动重编号）。"""
    parts = []
    for i, c in enumerate(cues, 1):
        parts.append(str(i))
        parts.append(f"{format_ts(c.start)} --> {format_ts(c.end)}")
        parts.extend(c.lines)
        parts.append("")
    text = "\n".join(parts)
    return ("\ufeff" + text) if bom else text


def read_srt(path: str) -> tuple[list[Cue], str]:
    """读取字幕文件，自动尝试多种编码。返回 (cues, 用到的编码)。"""
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return parse_srt(data.decode(enc)), enc
        except (UnicodeDecodeError, ValueError):
            continue
    raise ValueError(f"无法解码文件: {path}")


def write_srt(path: str | None, cues: list[Cue], bom: bool = False) -> None:
    text = format_srt(cues, bom=bom)
    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)


def fail(msg: str, code: int = 1) -> "sys.NoReturn":  # type: ignore[name-defined]
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------------------
# shift / scale / merge / split / clean
# ---------------------------------------------------------------------------

def cmd_shift(args) -> None:
    cues, _ = read_srt(args.input)
    try:
        delta = float(args.offset)
    except ValueError:
        fail(f"偏移量格式错误: {args.offset!r}，示例: +2.5 / -1.25")
    dms = int(round(delta * 1000))
    clamped = 0
    for c in cues:
        ns, ne = c.start + dms, c.end + dms
        if ns < 0:
            clamped += 1
            ns = 0
        if ne < 0:
            ne = 0
        if ne <= ns:  # 钳制后仍非法：保留至少 1ms
            ne = ns + 1
        c.start, c.end = ns, ne
    if clamped:
        print(f"提示: {clamped} 条字幕起点被钳制到 00:00:00,000", file=sys.stderr)
    write_srt(args.output, cues, bom=args.bom)


def cmd_scale(args) -> None:
    cues, _ = read_srt(args.input)
    try:
        factor = float(args.factor)
    except ValueError:
        fail(f"倍率格式错误: {args.factor!r}，示例: 1.1")
    if factor <= 0:
        fail("倍率必须大于 0")
    if not cues:
        fail("文件中没有字幕")
    if args.anchor == "center":
        center = (cues[0].start + cues[-1].end) / 2
        fn = lambda t: center + (t - center) * factor
    else:
        fn = lambda t: t * factor
    for c in cues:
        c.start, c.end = int(round(fn(c.start))), int(round(fn(c.end)))
        if c.end <= c.start:
            c.end = c.start + 1
    write_srt(args.output, cues, bom=args.bom)


def cmd_merge(args) -> None:
    a, _ = read_srt(args.a)
    b, _ = read_srt(args.b)
    try:
        gap = float(args.gap)
    except ValueError:
        fail(f"间隔格式错误: {args.gap!r}")
    if gap < 0:
        fail("间隔不能为负数")
    offset = (a[-1].end if a else 0) + int(round(gap * 1000))
    for c in b:
        c.start += offset
        c.end += offset
    write_srt(args.output, a + b, bom=args.bom)
    print(f"已合并: {len(a)} + {len(b)} = {len(a) + len(b)} 条，"
          f"b 整体后移 {format_dur(offset)}", file=sys.stderr)


def cmd_split(args) -> None:
    cues, _ = read_srt(args.input)
    try:
        limit = float(args.seconds)
    except ValueError:
        fail(f"时长格式错误: {args.seconds!r}")
    if limit <= 0:
        fail("时长必须大于 0")
    limit_ms = int(round(limit * 1000))
    chunks: list[list[Cue]] = []
    cur: list[Cue] = []
    for c in cues:
        if cur and (c.end - cur[0].start) > limit_ms:
            chunks.append(cur)
            cur = []
        cur.append(c)
    if cur:
        chunks.append(cur)

    import os.path as op
    out = args.output
    if out and (out.endswith("/") or (op.isdir(out))):
        prefix = op.join(out, op.splitext(op.basename(args.input))[0] + "-part")
    elif out:
        prefix = out
    else:
        prefix = op.splitext(args.input)[0] + "-part"
    if prefix.endswith(".srt"):
        prefix = prefix[:-4]
    for i, ch in enumerate(chunks, 1):
        path = f"{prefix}{i:02d}.srt"
        write_srt(path, ch, bom=args.bom)
        print(f"写入 {path}: {len(ch)} 条, 跨度 {format_dur(ch[-1].end - ch[0].start)}",
              file=sys.stderr)


TAG_RE = re.compile(r"<[^>]+>")          # HTML 标签
ASS_RE = re.compile(r"\{[^}]*\}")        # ASS 覆盖码
WS_RE = re.compile(r"[ \t\u3000]+")


def clean_line(line: str) -> str:
    line = ASS_RE.sub("", line)
    line = TAG_RE.sub("", line)
    line = line.replace("♪", "").replace("♫", "")
    line = WS_RE.sub(" ", line).strip()
    return line


def cmd_clean(args) -> None:
    cues, _ = read_srt(args.input)
    dropped = 0
    kept: list[Cue] = []
    for c in cues:
        lines = [clean_line(x) for x in c.lines]
        lines = [x for x in lines if x]
        # 去掉连续重复行
        dedup = [x for i, x in enumerate(lines) if i == 0 or x != lines[i - 1]]
        if not dedup:
            dropped += 1
            continue
        c.lines = dedup
        kept.append(c)
    write_srt(args.output, kept, bom=args.bom)
    print(f"清洗完成: 保留 {len(kept)} 条, 删除空字幕 {dropped} 条",
          file=sys.stderr)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def cmd_check(args) -> None:
    cues, enc = read_srt(args.input)
    errors: list[str] = []
    warns: list[str] = []
    for pos, c in enumerate(cues, 1):
        tag = f"#{pos}"
        if c.index != pos:
            errors.append(f"{tag}: 编号不连续（应为 {pos}，实际为 {c.index}）")
        if not c.text.strip():
            errors.append(f"{tag}: 空字幕（无文本）")
        if c.start >= c.end:
            errors.append(f"{tag}: 时间非法（开始 >= 结束）")
        if pos > 1:
            prev = cues[pos - 2]
            if prev.end > c.start:
                errors.append(f"{tag}: 与上一条重叠 {prev.end - c.start}ms")
        if c.duration > 0:
            chars = len(re.sub(r"\s+", "", c.text))
            cps = chars / (c.duration / 1000)
            if cps > args.cps:
                warns.append(f"{tag}: 语速过快 {cps:.1f} 字/秒（阈值 {args.cps}）")
            if c.duration < 800:
                warns.append(f"{tag}: 时长过短 {format_dur(c.duration)}（< 0.8s）")
    print(f"检查 {args.input}：共 {len(cues)} 条字幕（编码 {enc}）")
    for w in warns:
        print(f"[警告] {w}")
    for e in errors:
        print(f"[错误] {e}")
    if not errors and not warns:
        print("全部通过 ✓")
    elif not errors:
        print(f"无错误，{len(warns)} 条警告")
    else:
        print(f"发现 {len(errors)} 个错误，{len(warns)} 条警告")
        sys.exit(1)


# ---------------------------------------------------------------------------
# translate
# ---------------------------------------------------------------------------

def chat_complete(base_url: str, api_key: str, model: str,
                  texts: list[str], to_lang: str) -> list[str]:
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system",
             "content": ("You are a subtitle translator. Translate each input string "
                         f"to {to_lang}. Keep line breaks inside each string. "
                         "Return ONLY a JSON array of translated strings, "
                         "same order and same count as the input, no explanations.")},
            {"role": "user",
             "content": json.dumps(texts, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        fail(f"API 请求失败 HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        fail(f"网络请求失败: {e.reason}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        fail(f"API 返回解析失败: {e}")
    try:
        content = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        fail(f"API 返回结构异常: {str(data)[:200]}")
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        out = json.loads(content)
    except json.JSONDecodeError:
        fail(f"模型未返回合法 JSON 数组: {content[:200]}")
    if not isinstance(out, list) or len(out) != len(texts):
        fail(f"翻译结果数量不符（要 {len(texts)} 条，得 {len(out) if isinstance(out, list) else '非数组'}）")
    return [str(x) for x in out]


def cmd_translate(args) -> None:
    cues, _ = read_srt(args.input)
    if not cues:
        fail("文件中没有字幕")
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL",
                                               "https://api.openai.com/v1")
    batches = [cues[i:i + args.batch] for i in range(0, len(cues), args.batch)]
    if args.dry_run:
        print(f"[dry-run] 目标语言: {args.to}，模型: {args.model}")
        print(f"[dry-run] 接口: {base_url.rstrip('/')}/chat/completions")
        print(f"[dry-run] 共 {len(cues)} 条字幕，分 {len(batches)} 批发送 "
              f"(每批 {args.batch} 条)，时间轴保持不变")
        print(f"[dry-run] 第 1 批请求体预览（已隐去 key）：")
        preview = {
            "model": args.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": f"…翻译为 {args.to} 的系统提示…"},
                {"role": "user",
                 "content": json.dumps([c.text for c in batches[0]],
                                       ensure_ascii=False)},
            ],
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2)[:1200])
        print("[dry-run] 未发起任何网络请求")
        return
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("SUBFIX_API_KEY")
    if not api_key:
        fail("未找到 API key，请设置环境变量 OPENAI_API_KEY（或 SUBFIX_API_KEY）")
    done = 0
    for bi, batch in enumerate(batches, 1):
        print(f"翻译中 {bi}/{len(batches)} ...", file=sys.stderr)
        texts = [c.text for c in batch]
        results = chat_complete(base_url, api_key, args.model, texts, args.to)
        for c, t in zip(batch, results):
            c.lines = t.split("\n")
        done += len(batch)
    write_srt(args.output, cues, bom=args.bom)
    print(f"翻译完成: {done} 条 -> {args.to}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="subfix",
        description="subfix - SRT 字幕工具箱：移位 / 变速 / 合并 / 切分 / 清洗 / 翻译 / 检查")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="子命令")

    def add_io(sp, out_help="输出文件（缺省打印到 stdout）"):
        sp.add_argument("-o", "--output", default=None, help=out_help)
        sp.add_argument("--bom", action="store_true", help="输出带 BOM（兼容老播放器）")

    s = sub.add_parser("shift", help="整体平移时间轴")
    s.add_argument("input", help="输入 .srt 文件")
    s.add_argument("offset", help="偏移秒数，如 +2.5 / -1.25（负数向前，0 钳制）")
    add_io(s)
    s.set_defaults(func=cmd_shift)

    s = sub.add_parser("scale", help="按倍率缩放时间轴（如 25fps->24fps 用 1.0417）")
    s.add_argument("input", help="输入 .srt 文件")
    s.add_argument("factor", help="倍率，如 1.1")
    s.add_argument("--anchor", choices=["start", "center"], default="start",
                   help="缩放锚点：start=从 0 开始，center=以全片中点为锚（默认 start）")
    add_io(s)
    s.set_defaults(func=cmd_scale)

    s = sub.add_parser("merge", help="拼接两个字幕文件")
    s.add_argument("a", help="第一个 .srt 文件")
    s.add_argument("b", help="第二个 .srt 文件（会被整体后移）")
    s.add_argument("--gap", default="1.0", help="两段之间的间隔秒数（默认 1.0）")
    add_io(s, out_help="输出文件（缺省打印到 stdout）")
    s.set_defaults(func=cmd_merge)

    s = sub.add_parser("split", help="按时长切分成多个文件")
    s.add_argument("input", help="输入 .srt 文件")
    s.add_argument("seconds", help="每段最长秒数，如 30")
    s.add_argument("-o", "--output", default=None,
                   help="输出前缀或目录（默认: 输入文件名-partNN.srt）")
    s.add_argument("--bom", action="store_true", help="输出带 BOM")
    s.set_defaults(func=cmd_split)

    s = sub.add_parser("clean", help="清洗：去标签/覆盖码/♪、去重、删空、重编号")
    s.add_argument("input", help="输入 .srt 文件")
    add_io(s)
    s.set_defaults(func=cmd_clean)

    s = sub.add_parser("translate", help="用 LLM 翻译字幕文本（时间轴不变）")
    s.add_argument("input", help="输入 .srt 文件")
    s.add_argument("--to", default="en", help="目标语言（默认 en）")
    s.add_argument("--model", default="gpt-4o-mini", help="模型名（默认 gpt-4o-mini）")
    s.add_argument("--base-url", default=None,
                   help="OpenAI 兼容接口地址（默认 $OPENAI_BASE_URL 或官方）")
    s.add_argument("--batch", type=int, default=20, help="每批字幕条数（默认 20）")
    s.add_argument("--dry-run", action="store_true",
                   help="只打印将要发送的请求预览，不发起网络请求")
    add_io(s)
    s.set_defaults(func=cmd_translate)

    s = sub.add_parser("check", help="检查字幕问题：编号/重叠/空/语速")
    s.add_argument("input", help="输入 .srt 文件")
    s.add_argument("--cps", type=float, default=20,
                   help="语速警告阈值（字/秒，默认 20）")
    s.set_defaults(func=cmd_check)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except FileNotFoundError as e:
        fail(f"文件不存在: {e.filename}")
    except ValueError as e:
        fail(str(e))


if __name__ == "__main__":
    main()
