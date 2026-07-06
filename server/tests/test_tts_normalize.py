from app.integrations.tts_normalize import (
    StreamingTTSNormalizer,
    normalize_for_tts,
)


def _feed_all(*chunks: str) -> str:
    n = StreamingTTSNormalizer()
    out = [n.feed(c) for c in chunks]
    out.append(n.flush())
    return "".join(out)


# --- 1. links ---


def test_strips_link_keeps_text() -> None:
    assert normalize_for_tts("看 [文档](https://example.com) 里的细节。") == "看 文档 里的细节。"


def test_strips_link_in_longer_text() -> None:
    text = "参考 [Python 文档](https://docs.python.org) 的说明。"
    out = normalize_for_tts(text)
    assert "Python 文档" in out
    assert "https://" not in out


# --- 2. backticks ---


def test_strips_inline_code() -> None:
    assert normalize_for_tts("调用 `print(1)` 看看。") == "调用 print(1) 看看。"


def test_strips_multiple_inline_codes() -> None:
    assert normalize_for_tts("`foo` 和 `bar`") == "foo 和 bar"


# --- 3. line-start markers ---


def test_strips_dash_list_marker() -> None:
    text = "- 苹果\n- 香蕉\n- 樱桃"
    assert normalize_for_tts(text) == "苹果香蕉樱桃"


def test_strips_star_list_marker() -> None:
    text = "* 苹果\n* 香蕉"
    assert normalize_for_tts(text) == "苹果香蕉"


def test_strips_hash_header_marker() -> None:
    assert normalize_for_tts("# 标题") == "标题"
    assert normalize_for_tts("### 子节") == "子节"
    assert normalize_for_tts("###### 最深") == "最深"


def test_strips_indented_marker() -> None:
    assert normalize_for_tts("  - 项") == "项"


# --- 4. bold/italic ---


def test_strips_bold() -> None:
    assert normalize_for_tts("这是 **重点** 内容。") == "这是 重点 内容。"


def test_strips_italic() -> None:
    assert normalize_for_tts("这是 *强调* 内容。") == "这是 强调 内容。"


def test_strips_bold_and_italic_combined() -> None:
    text = "**很**重要和*也*重要"
    assert normalize_for_tts(text) == "很重要和也重要"


# --- 5. blank lines ---


def test_collapses_blank_line_to_period() -> None:
    # Original sentence-ending "。" is preserved; the blank line becomes an extra "。"
    # TTS treats "。。" as a longer sentence break, which is the desired pause.
    assert normalize_for_tts("第一段。\n\n第二段。") == "第一段。。第二段。"


def test_collapses_multiple_blank_lines_to_single_period() -> None:
    assert normalize_for_tts("第一段。\n\n\n\n第二段。") == "第一段。。第二段。"


def test_blank_line_without_original_period() -> None:
    assert normalize_for_tts("段落一\n\n段落二") == "段落一。段落二"


def test_keeps_single_newline_as_concat() -> None:
    # Single \n between non-blank lines collapses to nothing — TTS does its own
    # chunking and we want streaming and single-shot to produce identical output.
    assert normalize_for_tts("行一\n行二") == "行一行二"


# --- defensive safety nets ---


def test_silently_drops_fenced_code_block() -> None:
    text = "看下面：\n```python\nprint(1)\nprint(2)\n```\n结束。"
    out = normalize_for_tts(text)
    assert "以下是代码示例" not in out
    assert "print" not in out
    assert "结束" in out
    assert "看下面" in out


def test_silently_drops_tilde_fence() -> None:
    text = "前文\n~~~\nruby code\n~~~\n后文"
    out = normalize_for_tts(text)
    assert "ruby" not in out
    assert "前文" in out
    assert "后文" in out


def test_drops_horizontal_rule() -> None:
    assert normalize_for_tts("段落一\n\n---\n\n段落二") == "段落一。段落二"


def test_drops_underscore_hr() -> None:
    assert normalize_for_tts("段落一\n\n___\n\n段落二") == "段落一。段落二"


# --- end-to-end ---


def test_plain_passthrough() -> None:
    assert normalize_for_tts("你好，世界。") == "你好，世界。"


def test_empty_input() -> None:
    assert normalize_for_tts("") == ""


def test_strips_underscore_emphasis() -> None:
    text = "看 __这里__ 和 _那里_ 还有 my_var_name。"
    out = normalize_for_tts(text)
    assert out == "看 这里 和 那里 还有 my_var_name。"
    assert "my_var_name" in out


# --- streaming ---


def test_streaming_holds_partial_line() -> None:
    n = StreamingTTSNormalizer()
    a = n.feed("这是 **重")
    b = n.feed("点**。\n")
    c = n.flush()
    assert a == ""
    assert b == "这是 重点。"
    assert c == ""


def test_streaming_matches_whole_text() -> None:
    text = (
        "你好，**Hermes**。\n"
        "下面是清单：\n"
        "- 苹果\n"
        "- 香蕉\n"
        "代码：\n"
        "```python\n"
        "print(1)\n"
        "```\n"
        "更多 [详情](https://x.com)。"
    )
    whole = normalize_for_tts(text)
    streamed = _feed_all(*[text[i : i + 5] for i in range(0, len(text), 5)])
    assert streamed == whole


def test_streaming_collapses_blank_line_to_period() -> None:
    n = StreamingTTSNormalizer()
    a = n.feed("段落一\n\n")
    b = n.feed("段落二\n")
    c = n.flush()
    full = a + b + c
    assert full == "段落一。段落二"


def test_streaming_drops_code_block_silently() -> None:
    n = StreamingTTSNormalizer()
    a = n.feed("看：\n```py")
    b = n.feed("thon\nprint(1)\n```\n结束。")
    c = n.flush()
    full = a + b + c
    assert "print" not in full
    assert "以下是代码" not in full
    # The code block leaves a "gap" in speech, so a "。" is inserted before
    # the next non-blank line, matching the single-shot behavior.
    assert full == "看：。结束。"
