"""Bounded capture, the exec status header and secret redaction (Codex ports)."""

from app.agent.exec_output import HeadTailBuffer, exec_header, redact_secrets


def test_head_tail_buffer_keeps_both_ends_within_budget():
    buf = HeadTailBuffer(max_bytes=100)
    for i in range(1000):
        buf.write(f"line {i:04d}\n")
    text = buf.text()
    assert text.startswith("line 0000")
    assert text.rstrip().endswith("line 0999")
    assert "bytes omitted" in text
    assert buf.total == 1000 * 10
    assert buf.omitted == buf.total - 100


def test_head_tail_buffer_small_output_is_verbatim():
    buf = HeadTailBuffer(max_bytes=100)
    buf.write("hello\n")
    buf.write(b"world\n")
    assert buf.text() == "hello\nworld\n"
    assert buf.omitted == 0


def test_exec_header_puts_status_first():
    header = exec_header(exit_code=2, wall_ms=1530, status="Command exited with code 2.",
                         original_chars=5000, shown_chars=1000)
    lines = header.splitlines()
    assert lines[0] == "Command exited with code 2."
    assert "Exit code: 2" in lines
    assert "Wall time: 1.5 seconds" in lines
    assert any("showing 1000 of 5000" in line for line in lines)
    assert lines[-1] == "Output:"


def test_exec_header_omits_truncation_when_complete():
    assert "truncated" not in exec_header(exit_code=0, wall_ms=10, original_chars=10, shown_chars=10)


def test_redact_secrets_masks_credential_shapes():
    text = (
        "DEEPSEEK_API_KEY=sk-abcdefghijklmnopqrstuvwx\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345\n"
        "aws AKIAABCDEFGHIJKLMNOP\n"
        "gh ghp_abcdefghijklmnopqrstuvwxyz0123\n"
        "password = hunter2hunter2\n"
    )
    out = redact_secrets(text)
    assert "abcdefghijklmnopqrstuvwx" not in out
    assert "abcdefghijklmnopqrstuvwxyz012345" not in out
    assert "AKIAABCDEFGHIJKLMNOP" not in out
    assert "hunter2hunter2" not in out
    assert "sk-[REDACTED]" in out or "[REDACTED]" in out


def test_redact_secrets_leaves_ordinary_analysis_alone():
    text = "x = 3.14159\nmean token count: 512\nsk = 4\nThe eigenvalues are [1, 2, 3]."
    assert redact_secrets(text) == text
