from bftune.io.bbl import _Stream, _sx


def s(data: bytes) -> _Stream:
    return _Stream(data, 0, len(data))


def test_varint():
    assert s(bytes([0x96, 0x01])).uvb() == 150
    assert s(bytes([0x03])).svb() == -2  # zigzag
    assert s(bytes([0x04])).svb() == 2


def test_sign_extend():
    assert _sx(0x3, 2) == -1
    assert _sx(0x7F, 8) == 127
    assert _sx(0x80, 8) == -128


def test_tag2_3s32_small():
    # selector 00: three 2-bit values in one byte: 01 11 10 -> 1, -1, -2
    assert s(bytes([0b00_01_11_10])).tag2_3s32() == (1, -1, -2)


def test_tag8_8svb():
    # header bits select which of the values are present
    st = s(bytes([0b00000101, 0x02, 0x03]))
    assert st.tag8_8svb(3) == [1, 0, -2]


def test_tag8_4s16_nibbles():
    # selector 01 01 00 00 -> two 4-bit fields packed in one byte (high nibble first)
    st = s(bytes([0b00000101, 0x3F]))
    assert st.tag8_4s16_v2() == [3, -1, 0, 0]
