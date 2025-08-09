def sanitize_string_for_filename(input_str: str) -> str:
    """
    Sanitize a string to make it safe for use as a filename.

    Args:
        input_str: The input string to sanitize

    Returns:
        A sanitized string safe for use as a filename
    """
    result = []

    for c in input_str:
        if c in '<>:"|?*\\/':
            # Replace filesystem-unsafe characters with underscores
            result.append('_')
        elif c == ' ':
            # Replace spaces with hyphens for better readability
            result.append('-')
        elif c in "'`":
            # Remove apostrophes
            result.append('')
        elif c.isalnum() or c in '.-_':
            # Keep alphanumeric, dots, hyphens, and underscores
            result.append(c)
        else:
            # Replace any other special characters with underscores
            result.append('_')

    # Join the characters and ensure the filename doesn't start or end with dots
    sanitized = ''.join(result).strip('.')

    # Removing _ from the beginning or end of a filename
    sanitized = sanitized.strip('_')

    # Capitalize the first letter (must be the last thing)
    sanitized = sanitized[0].upper() + sanitized[1:]

    return sanitized
