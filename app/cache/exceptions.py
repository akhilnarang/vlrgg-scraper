import logging


class CacheMiss(Exception):
    def __init__(self, message: str):
        logging.info(f"Cache miss: {message}")
