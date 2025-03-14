from typing import Union, TypeVar
import click

# Type variable for numeric types to allow flexibility in validation
T = TypeVar('T', int, float)

def validate_positive(
    ctx: click.Context,
    param: click.Parameter,
    value: Union[T, None],
    allow_zero: bool = False
) -> Union[T, None]:
    """
    Validates that the provided numeric value is positive or non-negative.

    Designed as a Click callback for validating command-line parameters, this function ensures
    that the value meets the specified positivity requirement. If the value is invalid, it raises
    a `click.BadParameter` exception with a descriptive message.

    Args:
        ctx (click.Context): The Click context, automatically passed by Click.
        param (click.Parameter): The parameter being validated, automatically passed by Click.
        value (Union[T, None]): The numeric value to validate (int or float).
        allow_zero (bool): If True, allows zero as a valid value; if False, requires strictly
            positive values. Defaults to False.

    Returns:
        Union[T, None]: The validated value if it meets the criteria.

    Raises:
        click.BadParameter: If the value does not meet the positivity requirement or if the value
            is None when a value is expected.
    """
    if value is None:
        return None  # Allow None for optional parameters

    threshold = 0 if allow_zero else 0
    condition = value > threshold if not allow_zero else value >= threshold
    if not condition:
        requirement = "non-negative" if allow_zero else "positive"
        raise click.BadParameter(
            f"{param.name} must be {requirement}, got {value}"
        )
    return value

def validate_concurrency(
    ctx: click.Context,
    param: click.Parameter,
    value: Union[int, None],
    min_value: int = 1,
    max_value: int = 20
) -> Union[int, None]:
    """
    Validates that the concurrency parameter is within the specified range.

    Designed as a Click callback for validating command-line parameters, this function ensures
    that the concurrency value falls within the defined minimum and maximum bounds (inclusive).
    If the value is out of bounds or invalid, it raises a `click.BadParameter` exception with a
    detailed error message.

    Args:
        ctx (click.Context): The Click context, automatically passed by Click.
        param (click.Parameter): The parameter being validated, automatically passed by Click.
        value (Union[int, None]): The integer value representing concurrency.
        min_value (int): The minimum acceptable value for concurrency. Defaults to 1.
        max_value (int): The maximum acceptable value for concurrency. Defaults to 20.

    Returns:
        Union[int, None]: The validated concurrency value if it meets the criteria.

    Raises:
        click.BadParameter: If the value is not within the specified range or if the value is
            None when a value is expected.
    """
    if value is None:
        return None  # Allow None for optional parameters

    if not isinstance(value, int):
        raise click.BadParameter(
            f"{param.name} must be an integer, got {type(value).__name__}"
        )

    if value < min_value or value > max_value:
        raise click.BadParameter(
            f"{param.name} must be between {min_value} and {max_value}, got {value}"
        )
    return value
