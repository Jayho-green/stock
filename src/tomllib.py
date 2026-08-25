"""Python 3.10 兼容垫片:本机无 3.11 的标准库 tomllib,转发到已装的 tomli。"""

from tomli import TOMLDecodeError, load, loads

__all__ = ["TOMLDecodeError", "load", "loads"]
