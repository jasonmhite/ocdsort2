#!/usr/bin/env python3
import yaml
import click
import os
import tvnamer
import shutil
from tvnamer import utils
from fuzzywuzzy import process

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

@click.command()
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


    print_results(success, fail)

    if not dry:
        click.secho("\n")
        click.confirm("Proceed to move files?", abort=True)

        with click.progressbar(success) as bar:
            for file in bar:
                move_files(file)

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
    try:
        # Note: don't think the renamer in tvnamer works correctly

        new_path = os.path.join(
            config['destination'],
            info['identified_as'],
        )

        # See if directory exists, otherwise make it
        try:
            os.makedirs(new_path)
        except OSError as e:
            if e.errno != 17:
                raise

        new_full_name = os.path.join(new_path, info['new_name'])

        shutil.move(
            info['filename'],
            new_full_name,
        )

        if clean:
            os.unlink(info['filename'])

        info['moved_to'] = new_full_name

    except Exception as e:
        info['failed'] = True
        info['failure_reason'] = "Error moving file ({})".format(e)

def print_results(success, fail):
    click.secho("Successfully identified:")
    for item in success:
        click.secho("    {filename} -> {new_name}".format(**item))

    if len(fail) > 0:
        click.secho("\n")
        click.secho("Failures:")
        for item in fail:
            click.secho("    {filename} -> {failure_reason}".format(**item))

if __name__ == '__main__':
    sort()
