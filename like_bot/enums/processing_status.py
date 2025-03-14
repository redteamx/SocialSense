from enum import Enum, auto
from typing import Dict


class ProcessingStatus(Enum):
    """
    Represents the status of a processing task in the LikeBot application.

    This enumeration defines the possible states of a task, such as liking an Instagram post
    or tracking followers. Each status is associated with a string value for serialization
    and a human-readable description for documentation and debugging.

    Attributes:
        PENDING: Task is awaiting processing ("pending").
        LIKED: Task completed successfully, post was liked ("liked").
        SKIPPED: Task was skipped, e.g., private profile or all posts liked ("skipped").
        ERROR: An error occurred during processing ("error").
        RETRY: Task encountered a transient issue and is scheduled for retry ("retry").
    """
    PENDING = "pending"
    LIKED = "liked"
    SKIPPED = "skipped"
    ERROR = "error"
    RETRY = "retry"

    _descriptions: Dict["ProcessingStatus", str] = {
        PENDING: "Task is awaiting processing",
        LIKED: "Task completed successfully, post was liked",
        SKIPPED: "Task was skipped, e.g., private profile or all posts liked",
        ERROR: "An error occurred during processing",
        RETRY: "Task encountered a transient issue and is scheduled for retry",
    }

    @property
    def description(self) -> str:
        """
        Get the human-readable description of the status.

        Returns:
            str: A description of the current status.
        """
        return self._descriptions[self]

    @classmethod
    def from_string(cls, value: str) -> "ProcessingStatus":
        """
        Convert a string value to the corresponding ProcessingStatus.

        Args:
            value (str): The string value to convert (e.g., "pending").

        Returns:
            ProcessingStatus: The matching status enum member.

        Raises:
            ValueError: If the value does not match any status.
        """
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"Invalid processing status: '{value}'. Must be one of {list(cls._value2member_map_.keys())}")
