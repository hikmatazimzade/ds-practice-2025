from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class TransactionVerificationRequest(_message.Message):
    __slots__ = ("allFilled", "creditCardGood", "listItems")
    ALLFILLED_FIELD_NUMBER: _ClassVar[int]
    CREDITCARDGOOD_FIELD_NUMBER: _ClassVar[int]
    LISTITEMS_FIELD_NUMBER: _ClassVar[int]
    allFilled: bool
    creditCardGood: bool
    listItems: str
    def __init__(self, allFilled: bool = ..., creditCardGood: bool = ..., listItems: _Optional[str] = ...) -> None: ...

class TransactionVerificationResponse(_message.Message):
    __slots__ = ("empty", "creditCardState", "listItemsEmpty")
    EMPTY_FIELD_NUMBER: _ClassVar[int]
    CREDITCARDSTATE_FIELD_NUMBER: _ClassVar[int]
    LISTITEMSEMPTY_FIELD_NUMBER: _ClassVar[int]
    empty: bool
    creditCardState: bool
    listItemsEmpty: bool
    def __init__(self, empty: bool = ..., creditCardState: bool = ..., listItemsEmpty: bool = ...) -> None: ...
