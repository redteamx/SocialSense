from enum import Enum

class ProcessingStatus(Enum):
    """
    Represents the status of a processing task in the LikeBot application.

    Attributes:
        PENDING: The task is awaiting processing.
        LIKED: The task completed successfully, indicating that a post was liked.
        SKIPPED: The task was skipped (e.g., due to the profile being private or all posts already liked).
        ERROR: An error occurred during processing.
        RETRY: The task encountered a transient issue and is scheduled for a retry.
    """
    PENDING = "pending"
    LIKED = "liked"
    SKIPPED = "skipped"
    ERROR = "error"
    RETRY = "retry"

