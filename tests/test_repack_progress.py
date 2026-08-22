"""What a repack reports while it runs.

The wording is worth a test of its own because it was wrong for a long
time and nobody noticed. Both paths said "reading" about images they were
decoding, drawing on and re-encoding, and the ALD path said it about
copies it never read at all.
"""

from alice_censor.repack_progress import RepackProgress, describe


def test_the_first_line_says_how_much_work_there_is():
    text = describe(RepackProgress(1, 529, 3154, "folder／image.png"))

    assert text.splitlines()[0] == "  529 to process, 3154 copied unchanged"


def test_later_lines_are_just_the_image():
    text = describe(RepackProgress(2, 529, 3154, "folder／image.png"))

    assert text == "  processing 2 of 529  folder／image.png"


def test_the_counter_says_where_the_run_is_up_to():
    text = describe(RepackProgress(529, 529, 3154, "last.png"))

    assert "processing 529 of 529" in text


def test_the_path_is_given_whole():
    """Trimming to the filename makes half a real archive ambiguous, since
    hundreds of images share a name and differ only by folder."""
    text = describe(RepackProgress(2, 3, 0, "キャラ／アイス／基本.png"))

    assert text.endswith("キャラ／アイス／基本.png")
