"""
{name} — smoke test.

`main.py` is the file the build check runs, so it must exit rather than serve.
It exercises the storage layer end to end against a throwaway database and
prints what happened, which means a broken schema or a broken query surfaces
as a traceback the moment the project is built, not the first time someone
opens the browser.

    python main.py       check that the pieces work
    python server.py     actually run it
"""

import os
import sys
import tempfile

import server


def main():
    # A real file rather than :memory:, because that is what production uses
    # and an in-memory database hides file and path mistakes.
    scratch = os.path.join(tempfile.mkdtemp(), 'smoke.db')
    server.DB = scratch

    server.setup()
    print('schema     ok')

    item, err = server.add_item('First item')
    assert err is None and item['id'], 'add failed: %s' % err
    print('insert     ok  ->', item['title'])

    blank, err = server.add_item('   ')
    assert blank is None and err, 'a blank title should have been refused'
    print('validation ok  ->', err)

    rows = server.list_items()
    assert len(rows) == 1, 'expected one row, got %d' % len(rows)
    print('read       ok  ->', len(rows), 'item')

    done, err = server.set_done(item['id'], True)
    assert err is None and done['done'] == 1, 'mark-done failed'
    print('update     ok  -> done =', done['done'])

    gone, _ = server.remove_item(item['id'])
    assert gone and server.list_items() == [], 'delete failed'
    print('delete     ok  -> empty')

    print()
    print('{name}: storage layer is sound. Run `python server.py` to use it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
