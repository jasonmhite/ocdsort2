from unittest import mock
from copy import deepcopy as copy
from voluptuous import Invalid
from nose.tools import raises
from io import StringIO, TextIOWrapper

import ocdsort

CONFIG = """
config:
    valid_extensions:
        - mkv
    destination: /test
    threshold: 85
    user:
        uid: 1001
        gid: 5000
        mode: '755'
shows:
    some show: null
"""

PARSED_CONFIG = {
    "config": {
        "valid_extensions": ['mkv'],
        "destination": "/test",
        "threshold": 85,
        "user": {
            "uid": 1001,
            "gid": 5000,
            "mode": "755",
        },
    },
    "shows": {
        "some show": {
            "season": 1,
            "offset": 0,
            "names": [],
        },
    },
}

def test_config():
    with mock.patch('ocdsort.open', mock.mock_open(read_data=CONFIG), create=True):
        assert ocdsort.config == PARSED_CONFIG["config"]
        assert ocdsort.shows == PARSED_CONFIG["shows"]

def test_validate():
    ocdsort.configSchema(PARSED_CONFIG)

@raises(Invalid)
def test_invalid():
    ocdsort.configSchema({})

files = ["[Blah] some show - 01.mkv"]

PARSED = ocdsort.default_entry()

PARSED.update(**{
    "episodename": None,
    "filename": files[0],
    "seriesname": "some show",
    "failure_reason": None,
    "failed": False,
    "ext": '.mkv',
    "episode": "01",
})

IDENTIFIED = copy(PARSED)
IDENTIFIED.update(confidence=100, identified_as="some show")

NAMED = copy(IDENTIFIED)
NAMED.update(
    new_name='some show - S1E01.mkv',
    season=1,
    offset=0,
)


def test_parse():
    global test_info
    test_info = list(ocdsort.parse(files))
    assert test_info == [PARSED]

def test_identify():
    global test_identified
    test_identified = list(ocdsort.identify(test_info))
    assert test_identified == [IDENTIFIED]

def test_generate_names():
    global test_names
    test_names = list(ocdsort.generate_names(test_identified))

    print(test_names)

    assert test_names == [NAMED]

FINAL_NAME = "/test/some show/some show - S1E01.mkv"

# This test is broken with the refactor of move_files
# Have to mock the progressbar context manager, pain in the arse
#@mock.patch('ocdsort.shutil.move')
#@mock.patch('ocdsort.os.unlink')
#@mock.patch('ocdsort.os.makedirs')
#def test_move_files(mock_makedirs, mock_unlink, mock_move):
    #ocdsort.move_files(test_names)

    #mock_makedirs.assert_called()
    #mock_unlink.assert_called()
    #mock_move.assert_called_with(
        #NAMED['filename'],
        #FINAL_NAME
    #)

mock_echo = mock.Mock('ocdsort.click.secho')
OUTPUT = """Successfully identified:
    [Blah] some show - 01.mkv -> some show - S1E01.mkv

Failures:
    [Blah] some show - 01.mkv -> Test failure"""

def test_print_results():
    with mock.patch('ocdsort.click.secho', mock_echo) as m:
        success = [NAMED]
        failure = [copy(NAMED)]
        failure[0].update(failed=True, failure_reason="Test failure")
        ocdsort.print_status(success + failure)

        assert m.call_args_list == [mock.call(s) for s in OUTPUT.split('\n')]


