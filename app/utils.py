from app.constants import PREFIX, VLR_IMAGE


def get_image_url(img: str) -> str:
    """
    Determine an image URL based on the string
    :param img: The src string of the image
    :return: The full URL
    """
    if img == VLR_IMAGE:
        return f"{PREFIX}/{img}"
    else:
        return f"https:{img}"
