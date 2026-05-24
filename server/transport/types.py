from __future__ import annotations
from typing import TypeVar, ParamSpec, Callable, Coroutine, Any


R = TypeVar("R")
P = ParamSpec("P")

AsyncCallback = Callable[P, Coroutine[Any, Any, R]]
"""Generic async callback type.
P is the parameter specification, R is the return type.
"""

SyncCallback = Callable[P, R]
"""Generic synchronous callback type.
P is the parameter specification, R is the return type.
"""

Callback = Callable[P, Coroutine[Any, Any, R]] | Callable[P, R]
"""Generic callback type that can be either async or sync.
P is the parameter specification, R is the return type.
"""
