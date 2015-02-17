#!/usr/bin/env python3.4
import yaml
import click
import os
import tvnamer
import shutil
import tvdb_api
import arrow
import sys
from tvnamer import utils
from fuzzywuzzy import process
from tvdb_api.tvdb_exceptions import tvdb_exception

confdir = os.path.join(
    os.getenv('HOME'),
    'ocdsort.yml',
)

@click.group()
def main():
    pass

def parse_config(filename):
    with open(filename, 'r') as f:
        config = yaml.load(f.read())

    return config['config'], config['shows']

def build_index(shows):
    aliases = {}
    # Invert the dictionary
    for key, value in shows.items():
        aliases[key] = key
        try:
            for alias in value['names']:
                aliases[alias] = key
        except (KeyError, TypeError):
            continue

    return aliases

config, shows = parse_config(confdir)
all_shows = build_index(shows)

@main.command()
@click.argument('path', type=click.Path(exists=True))
@click.option('--dry', is_flag=True)
def sort(path, dry):
    filenames = utils.FileFinder(
        path,
        with_extension=config['valid_extensions'],
        recursive=True,
    ).findFiles()

    results = list(generate_names(identify(parse(filenames))))

    success = list(filter(lambda s: not s['failed'], results))
    fail = list(filter(lambda s: s['failed'], results))

    if len(success) == 0 and len(fail) == 0:
        print("No files found")
        sys.exit(0)

    print_results(success, fail)

    if not dry:
        click.secho("\n")
        click.confirm("Proceed to move files?", abort=True)

        to_chown = []

        with click.progressbar(success) as bar:
            for file in bar:
                to_chown += move_files(file)

    print("Setting permissions")
    for file in to_chown:
        shutil.chown(
            file,
            user=config['user']['uid'],
            group=config['user']['gid'],
        )
        os.chmod(
            file,
            int(config['user']['mode'], 8),
        )

def parse(filenames):
    for filename in filenames:
        try:
            r = utils.FileParser(filename).parse()
            info = r.getepdata()

            info['failed'] = False
            info['failure_reason'] = None

            if info['episode'] is None:
                raise KeyError("Could not parse episode number")

            elif info['seriesname'] is None:
                raise KeyError("Could not parse series name")

            else:
                info['filename'] = filename
                yield info

        except (tvnamer.tvnamer_exceptions.InvalidFilename, KeyError) as e:
            # This type of error is a bit different, so this file
            # will be ignored and won't be reported.
            click.secho(
                "Error processing {} -> {}".format(
                        os.path.basename(filename),
                        e
                    )
            )
            continue

def identify(episodes):
    for info in episodes:
        if not info['failed']:
            alias_name, confidence = process.extractOne(
                info['seriesname'],
                all_shows.keys(),
            )

            if confidence > config['threshold']:
                info["identified_as"] = all_shows[alias_name]
                info["confidence"] = confidence

            else:
                info['failed'] = True
                info['failure_reason'] = "Series not identified (confidence={})".format(confidence)

        yield info

def generate_names(episodes):
    for info in episodes:
        if not info['failed']:
            try:
                title = info['identified_as']
                extension = info['ext']

                # Check for optional overrides
                try:
                    info['offset'] = shows[title]['episode_offset']
                    episode = int(info['episode']) + info['offset']
                except (KeyError, TypeError):
                    info['offset'] = 0
                    episode = int(info['episode'])

                try:
                    info['season'] = int(shows[title]['season'])
                except (KeyError, TypeError):
                    info['season'] = 1

                info["new_name"] = utils.makeValidFilename(
                    "{identified_as} - S{season}E{episode}{ext}".format(**info),
                )

            except Exception as e:
                info['failed'] = True
                info['failure_reason'] = "Error during name generation ({})".format(e)

        yield info

def move_files(info, clean=True):
    to_chown = []
    try:
        # Note: don't think the renamer in tvnamer works correctly

        new_path = os.path.join(
            config['destination'],
            info['identified_as'],
        )

        # See if directory exists, otherwise make it
        try:
            os.makedirs(new_path)
            to_chown.append(new_path)
        except OSError as e:
            if e.errno != 17:
                raise

        new_full_name = os.path.join(new_path, info['new_name'])

        shutil.move(
            info['filename'],
            new_full_name,
        )

        to_chown.append(new_full_name)

        if clean:
            os.unlink(info['filename'])

        info['moved_to'] = new_full_name

    except Exception as e:
        info['failed'] = True
        info['moved_to'] = None
        info['failure_reason'] = "Error moving file ({})".format(e)

    return to_chown


def print_results(success, fail):
    if len(success) > 0:
        click.secho("Successfully identified:")
        for item in success:
            fname = os.path.basename(item['filename'])
            click.secho("    {} -> {}".format(fname, item['new_name']))

    if len(fail) > 0:
        click.secho("\n")
        click.secho("Failures:")
        for item in fail:
            click.secho("    {} -> {failure_reason}".format(
                fname,
                item['failure_reason'],
            ))

@main.command()
def missing():
    filenames = utils.FileFinder(
        config['destination'],
        with_extension=config['valid_extensions'],
        recursive=True,
    ).findFiles()

    results = list(grab_tvdb(identify(parse(filenames))))

    success = list(filter(lambda s: not s['failed'], results))
    fail = list(filter(lambda s: s['failed'], results))

    print(success)

def grab_tvdb(episodes):
    T = tvdb_api.Tvdb()

    for info in episodes:
        if not info['failed']:
            show_config = shows[info['identified_as']]

            try:
                C = show_config['tvdb']
                tvdb_id = C['id']

                tvdb_season = C['season'] if 'season' in C else 1
                tvdb_offset = C['offset'] if 'offset' in C else 0

                try:
                    show = T[tvdb_id]
                    ep = show[tvdb_season][int(info['episode']) + tvdb_offset]

                    info['tvdb_meta'] = ep

                except tvdb_exception as e:
                    info['failed'] = True
                    info['failure_reason'] = "TVDB lookup failure ({})".format(e)

            except KeyError:
                info['failed'] = True
                info['failure_reason'] = "No TVDB info provided or id not provided"

        yield info

if __name__ == '__main__':
    main()
