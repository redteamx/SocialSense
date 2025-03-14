import click

def validate_positive(ctx: click.Context, param: click.Parameter, value: float) -> float:
    """
    Validates that the provided numeric value is positive.

    This function is intended for use as a Click callback to validate command-line
    parameters. It ensures that the value is greater than zero; otherwise, it raises a
    click.BadParameter exception with a descriptive error message.

    :param ctx: Click context (automatically passed by Click).
    :param param: The parameter being validated.
    :param value: The numeric value to validate.
    :return: The validated positive value.
    :raises click.BadParameter: If the value is not positive.
    """
    if value <= 0:
        raise click.BadParameter(f"{param.name} must be positive, got {value}")
    return value

def validate_concurrency(ctx: click.Context, param: click.Parameter, value: int) -> int:
    """
    Validates that the concurrency parameter is within the acceptable range.

    This function checks that the provided concurrency value is between 1 and 20, inclusive.
    It is designed to be used as a Click callback for validating command-line parameters.
    If the value is out of bounds, it raises a click.BadParameter exception with a detailed
    error message.

    :param ctx: Click context (automatically passed by Click).
    :param param: The parameter being validated.
    :param value: The integer value representing concurrency.
    :return: The validated concurrency value.
    :raises click.BadParameter: If the value is not between 1 and 20.
    """
    if value < 1 or value > 20:
        raise click.BadParameter(f"{param.name} must be between 1 and 20, got {value}")
    return value

