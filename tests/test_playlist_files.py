import pytest

from sceneaccess.playlist_files import playlist_options, video_key


def choice(identifier='abcdefghijk'):
    return {'id': identifier, 'url': f'https://www.youtube.com/watch?v={identifier}', 'title': 'Video',
            'filename': f'Video [{identifier}].mp4', 'info': {}}


def test_youtube_aliases_have_the_same_identity():
    assert video_key('https://youtu.be/abcdefghijk') == video_key('https://www.youtube.com/watch?v=abcdefghijk&list=PL1')
    assert video_key('https://unrelated.example/watch?v=abcdefghijk') != video_key(choice()['url'])


def test_file_with_chat_hidden_but_orphan_offered_without_download(tmp_path):
    video = tmp_path/'Video [abcdefghijk].mp4'
    video.write_bytes(b'video')
    entries = [choice()]
    assert playlist_options(entries, tmp_path, [])[0]['local_path'] == video
    chats = [{'source_url': choice()['url'], 'source_path': str(video)}]
    assert playlist_options(entries, tmp_path, chats) == []
    video.unlink()
    offered = playlist_options(entries, tmp_path, chats)
    assert len(offered) == 1 and offered[0]['local_path'] is None


@pytest.mark.parametrize('prefix', ['', '[' + 'a'*32 + '] '])
def test_expected_custom_filename_is_reused_without_a_catalog(tmp_path, prefix):
    video = tmp_path/(prefix + 'Custom name.mp4')
    video.write_bytes(b'video')
    entry = {**choice(), 'filename': 'Custom name.mp4'}
    assert playlist_options([entry], tmp_path, [])[0]['local_path'] == video
    video.unlink()
    assert playlist_options([entry], tmp_path, [])[0]['local_path'] is None


def test_partial_streams_and_zero_length_files_are_not_reused(tmp_path):
    for name in ('Video [abcdefghijk].f137.mp4', 'Video [abcdefghijk].temp.mp4', 'Video [abcdefghijk].mp4'):
        (tmp_path/name).write_bytes(b'' if name.endswith('].mp4') else b'partial')
    assert playlist_options([choice()], tmp_path, [])[0]['local_path'] is None


def test_outside_download_folder_does_not_hide_a_playlist_entry(tmp_path):
    folder = tmp_path/'downloads'
    folder.mkdir()
    video = tmp_path/'Video [abcdefghijk].mp4'
    video.write_bytes(b'video')
    chats = [{'source_url': choice()['url'], 'source_path': str(video)}]
    assert playlist_options([choice()], folder, chats)[0]['local_path'] is None


def test_bracket_in_title_does_not_identify_another_video(tmp_path):
    video = tmp_path/'Review [bbbbbbbbbbb] [aaaaaaaaaaa].mp4'
    video.write_bytes(b'Video A')
    assert playlist_options([choice('bbbbbbbbbbb')], tmp_path, [])[0]['local_path'] is None


def test_known_chat_identity_wins_over_title_collision(tmp_path):
    video = tmp_path/'Same title.mp4'
    video.write_bytes(b'Video A')
    chats = [{'source_path': str(video), 'source_url': choice('aaaaaaaaaaa')['url']}]
    b = {**choice('bbbbbbbbbbb'), 'filename': 'Same title.mp4'}
    offered = playlist_options([b], tmp_path, chats)
    assert len(offered) == 1 and offered[0]['local_path'] is None


def test_ambiguous_orphan_filename_is_not_assigned_to_either_video(tmp_path):
    video = tmp_path/'Same title.mp4'
    video.write_bytes(b'unknown video')
    entries = [{**choice(key), 'filename': video.name} for key in ('aaaaaaaaaaa', 'bbbbbbbbbbb')]
    offered = playlist_options(entries, tmp_path, [])
    assert len(offered) == 2 and all(entry['local_path'] is None for entry in offered)


@pytest.mark.parametrize('title', ['lecture.part.one', 'lecture.temp.one', 'lecture.f2026.one',
                                   'lecture.temp', 'lecture.part', 'lecture.f137'])
@pytest.mark.parametrize('with_id', [False, True])
def test_partial_like_title_is_not_a_technical_extension(tmp_path, title, with_id):
    video = tmp_path/(title + (' [abcdefghijk]' if with_id else '') + '.mp4')
    video.write_bytes(b'complete video')
    assert playlist_options([{**choice(), 'filename': video.name}], tmp_path, [])[0]['local_path'] == video
