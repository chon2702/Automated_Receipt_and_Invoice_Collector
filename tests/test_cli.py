def test_validate_and_list_without_network(config_file, capsys):
    from goco.view.cli import main
    assert main(['--config', str(config_file), 'config', 'validate']) == 0
    assert main(['list-services', '--config', str(config_file)]) == 0
    assert 'sample' in capsys.readouterr().out


def test_invalid_config_returns_two(config_file, capsys):
    from goco.view.cli import main
    config_file.write_text('{}')
    assert main(['--config', str(config_file), 'list-services']) == 2
    captured = capsys.readouterr()
    assert captured.err
    assert not captured.out
