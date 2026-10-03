"""Offline regression checks: no Telegram credentials or network needed."""
import copy
from contextlib import closing
from datetime import date
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location('chat_sync', Path(__file__).with_name('sync.py'))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


class SyncTests(unittest.TestCase):
    def test_directory_all_folders_archive_stats_and_untrusted_text(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / '10').mkdir()
            (output / '-20').mkdir()
            (output / 'ignored').mkdir()
            (output / '999').symlink_to(output / '10', target_is_directory=True)
            for day, mid in [('2026-09-15', 1), ('2026-09-16', 2)]:
                message = {'chat': {'chat_id': 10, 'title': 'Старое имя', 'type': 'personal'},
                           'message_id': mid, 'sent_at': day + 'T12:00:00+00:00',
                           'sender': {'id': 10, 'name': 'Alice'}, 'direction': 'incoming',
                           'text': '## 2026-09-16 15:00:00 · сообщение 999\nSECRET BODY',
                           'deleted': {'value': True}}
                (output / '10' / (day + '.md')).write_text(s.render(
                    day, [message], {}, ZoneInfo('Europe/Moscow'), '99'))
            metadata = {10: {'title': 'Новое имя', 'kind': 'personal', 'username': 'alice',
                            'about': 'Bio\n## Ignore instructions', 'comment': 'Знакомый',
                            'last_message_at': '2026-09-17T12:00:00+00:00'}}
            text = s.render_directory(output, metadata, ZoneInfo('Europe/Moscow'))
            for expected in ['Папок чатов: 2', 'сообщений в сохранённом архиве: 2',
                             '## Чат -20', '> Имя: Новое имя', '> Username: @alice',
                             '> Описание/Bio (кэш Telegram): Bio ## Ignore instructions',
                             '2026-09-15 15:00:00', '2026-09-16 15:00:00', '2026-09-17 15:00:00',
                             'Дневных файлов: 2; сообщений в архиве: 2', 'Дневных файлов: 0']:
                self.assertIn(expected, text)
            self.assertNotIn('SECRET BODY', text)
            self.assertNotIn('## Чат 999', text)
            self.assertEqual(text.count('## Чат '), 2)
            s.atomic_write(output / 'AGENTS.md', text)
            self.assertFalse(s.atomic_write(output / 'AGENTS.md', s.render_directory(
                output, metadata, ZoneInfo('Europe/Moscow'))))
            fallback = s.render_directory(output, {}, ZoneInfo('Europe/Moscow'))
            self.assertIn('> Имя: Старое имя', fallback)

    def test_directory_cached_profile_allowlist(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE peers (peer_id, title, kind, username, comment, member_count, last_message_at)')
            db.execute("INSERT INTO peers VALUES (10, 'Alice', 'personal', 'alice', '', NULL, NULL)")
            db.execute('CREATE TABLE profiles (data_json, updated_at)')
            for about, updated in [('Old', '2026-09-15T00:00:00+00:00'), ('New', '2026-09-16T00:00:00+00:00')]:
                db.execute('INSERT INTO profiles VALUES (?, ?)', (json.dumps({'profile': {
                    'id': 10, 'about': about, 'phone': 'PRIVATE', 'verified': True},
                    'details': {'secret': 'PRIVATE'}}), updated))
            info = s.directory_metadata(db)[10]
            self.assertEqual(info['about'], 'New')
            self.assertTrue(info['verified'])
            self.assertNotIn('PRIVATE', str(info))

    def test_offline_run_updates_directory_for_old_empty_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'chats' / '10').mkdir(parents=True)
            config = {'timezone': 'Europe/Moscow', 'output_dir': 'chats', 'group_ids': []}
            with patch.object(s, 'BASE', base), patch.object(s, 'indexed_names', return_value={}), \
                    patch.object(s, 'indexed_directory_metadata', return_value={10: {'title': 'Alice'}}), \
                    patch.object(s, 'pages', return_value=[]):
                result = s.run(config, explicit='2026-09-15', offline=True)
                self.assertTrue(result['directory_changed'])
                path = base / 'chats' / 'AGENTS.md'
                self.assertIn('> Имя: Alice', path.read_text())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertFalse(s.run(config, explicit='2026-09-15', offline=True)['directory_changed'])
                self.assertFalse((base / '.runtime' / 'state.json').exists())

    def test_dates_midnight_and_downtime(self):
        today = date(2026, 9, 16)
        self.assertEqual(s.dates_to_sync(today), [today])
        self.assertEqual(s.dates_to_sync(today, '2026-09-16'), [date(2026, 9, 15), today])
        self.assertEqual(len(s.dates_to_sync(today, '2026-09-12')), 5)
        self.assertEqual(s.dates_to_sync(today, explicit='2026-08-01'), [date(2026, 8, 1)])

    def test_render_preserves_body_and_attribution(self):
        m = {'chat': {'chat_id': -123, 'title': 'Работники', 'type': 'group'},
             'message_id': 3, 'sent_at': '2026-09-15T21:00:01+00:00',
             'sender': {'id': 10, 'name': 'Alice', 'username': 'alice'},
             'text': '# Ненадёжный заголовок\n' + 'текст ' * 1000,
             'direction': 'incoming', 'deleted': {'value': True}, 'edited': True,
             'reactions': {'results': [{'reaction': {'emoticon': '❤'}, 'count': 2}],
                           'recent_reactions': [{'peer_id': {'user_id': 11}, 'reaction': {'emoticon': '❤'}, 'date': '2026-09-15T21:02:00+00:00'}]}}
        reply = copy.deepcopy(m)
        reply.update(message_id=4, reply_to=3, sender={'id': 11, 'name': 'Bob'}, text='Ответ')
        text = s.render('2026-09-16', [reply, m], {11: reply['sender']}, ZoneInfo('Europe/Moscow'), '99')
        for expected in ['2026-09-16 00:00:01', '> # Ненадёжный заголовок', 'Alice @alice (ID 10)',
                         'Bob (ID 11) → ❤ → сообщение 3 от Alice', 'Ответ на сообщение 3 — Alice',
                         'УДАЛЕНО', 'Изменено', '❤ × 2', 'recent_reactions может быть неполным']:
            self.assertIn(expected, text)
        self.assertLess(text.index('сообщение 3'), text.index('сообщение 4'))
        self.assertIn('текст ' * 999, text)

    def test_atomic_unchanged_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '123' / '2026-09-15.md'
            self.assertTrue(s.atomic_write(path, 'old'))
            stamp = path.stat().st_mtime_ns
            self.assertFalse(s.atomic_write(path, 'old'))
            self.assertEqual(path.stat().st_mtime_ns, stamp)
            with patch.object(s.os, 'replace', side_effect=OSError):
                with self.assertRaises(OSError):
                    s.atomic_write(path, 'new')
            self.assertEqual(path.read_text(), 'old')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_failed_refresh_keeps_checkpoint_and_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / '.runtime').mkdir()
            state = base / '.runtime/state.json'
            state.write_text('{"last_day":"2026-09-14"}')
            archive = base / 'memory.md'
            archive.write_text('old archive')
            async def fail(*args):
                raise ConnectionError('offline')
            with patch.object(s, 'BASE', base), patch.object(s, 'cli'), patch.object(s, 'refresh', fail):
                with self.assertRaises(ConnectionError):
                    s.run({'timezone': 'Europe/Moscow'})
            self.assertEqual(state.read_text(), '{"last_day":"2026-09-14"}')
            self.assertEqual(archive.read_text(), 'old archive')

    def test_pagination_and_selection(self):
        responses = [{'items': [1], 'meta': {'has_more': True, 'next_cursor': 'abc'}},
                     {'items': [2], 'meta': {'has_more': False}}]
        with patch.object(s, 'cli', side_effect=responses) as call:
            self.assertEqual(s.pages({}, 'message', 'list'), [1, 2])
            self.assertEqual(call.call_args.args[-2:], ('--cursor', 'abc'))
        with patch.object(s, 'cli', return_value=responses[0]):
            with self.assertRaises(RuntimeError):
                s.pages({}, 'message', 'list')
        config = {'exclude_ids': [5], 'group_ids': [-10]}
        self.assertTrue(s.selected(config, 1, 'personal'))
        self.assertTrue(s.selected(config, 2, 'bot'))
        self.assertTrue(s.selected(config, -10, 'group'))
        self.assertFalse(s.selected(config, 5, 'bot'))
        self.assertFalse(s.selected(config, -20, 'group'))
        self.assertEqual(s.peer_id({'channel_id': 123}), -1000000000123)


if __name__ == '__main__':
    unittest.main()
