#!/usr/bin/env python3.4
import yaml
import click
import os
import tvnamer
import shutil
from tvnamer import utils
from fuzzywuzzy import process
import sys
from lazy_object_proxy import Proxy

confdir = os.path.join(
    os.getenv('HOME'),
    'ocdsort.yml',
)

def lazy(*args, **kwargs):
    def call_f(f):
        return lambda: f(*args, **kwargs)

    return lambda f: Proxy(call_f(f))

@lazy(confdir)
def global_config(filename):
    with open(filename) as f:
        conf = yaml.load(f.read())

    return conf

@lazy(global_config)
def config(c):
    return c['config']

@lazy(global_config)
def shows(c):
    return c['shows']

@click.group()
def main():
    pass

@lazy(shows)
def all_shows(shows):
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

@click.command()
@click.argument('path', type=click.Path(exists=True))
@click.option('--dry', is_flag=True)
def sort(path, dry):
    do_sort(path, dry)

def do_sort(path, dry):
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
        click.confirm("Proceed to move files?", abort=True)

        to_chown = []

        nsuccess = len(success)
        with click.progressbar(length=nsuccess) as bar:
            for file in success:
                to_chown += move_files(file)
                bar.update(1)

    click.secho("Setting permissions")
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
            info['failed'] = True
            info['failure_reason'] = "Error parsing {} -> {}".format(
                os.path.basename(filename),
                e
            )

            yield info

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
    # notice: this one does not loop over info, because the main loop prints
    # the progress bar
    to_chown = []
    try:
        # Note: don't think the renamer in tvnamer works correctly

        new_path = os.path.join(
            config['destination'],
            info['identified_as'],
        )
        print(new_path)

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

        click.secho("")

    if len(fail) > 0:
        click.secho("Failures:")
        for item in fail:
            fname = os.path.basename(item['filename'])
            click.secho("    {} -> {}".format(
                fname,
                item['failure_reason'],
            ))
        click.secho("")

if __name__ == '__main__':
    sort()
