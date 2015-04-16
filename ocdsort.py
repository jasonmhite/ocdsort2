#!/usr/bin/env python3.4
import yaml
import click
import os
import tvnamer
import shutil
import sys

from itertools import product
from voluptuous import *
from tvnamer import utils
from fuzzywuzzy import process
from lazy_object_proxy import Proxy

confdir = os.path.join(
    os.getenv('HOME'),
    'ocdsort.yml',
)

showSchema = Schema({
    Required("season", default=1): int,
    Required("offset", default=0): int,
    Required("names", default=[]): [str]
})

valid_chmods = ["".join(map(str, i)) for i in product(range(10), repeat=3)]

def checkChmod(t):
    if t not in valid_chmods:
        raise Invalid("{} not valid chmod mode".format(t))

    return t

def coerceNone(t):
    if t is None:
        return showSchema({})
    elif type(t) is dict:
        return showSchema(t)
    else: raise Invalid("Invalid show entry")

configSchema = Schema({
    Required('config'): Schema({
        Required("valid_extensions"): [str],
        Required("destination"): str,
        Required("threshold", default=85): Range(min=1, max=100),
        Optional("user"): Schema({
            Required("uid"): Range(min=0),
            Required("gid"): Range(min=0),
            Required("mode"): checkChmod,
        }),
    }),
    Required('shows'): Schema({Required(str): coerceNone}),
})

def lazy(*args, **kwargs):
    def call_f(f):
        return lambda: f(*args, **kwargs)

    return lambda f: Proxy(call_f(f))

@lazy(confdir)
def global_config(filename):
    with open(filename) as f:
        conf = yaml.load(f.read())

    return configSchema(conf)

@lazy(global_config)
def config(c):
    return c['config']

@lazy(global_config)
def shows(c):
    return c['shows']

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

def filtered(f):
    def f_filtered(items):
        for item in items:
            if item['failed']:
                yield item
            else:
                yield f(item)

    return f_filtered

default_entry = lambda: {
    "episodename": None,
    "filename": None,
    "seriesname": None,
    "failure_reason": None,
    "failed": False,
    "ext": None,
    "episode": None,
    "confidence": None,
    "identified_as": None,
    "new_name": None,
    "season": None,
    "offset": None
}

@click.group()
def main():
    pass

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

    print_status(results)

    if not dry:
        click.confirm("Proceed to move files?", abort=True)

        #nsuccess = len([i for i in results if not i['failed']])
        #with click.progressbar(length=nsuccess) as bar:
            #for info in filter(lambda s: not s['failed'], results):
                #move_files(info)
                #bar.update(1)

        to_chown = move_files(info)

        if "user" in config:
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
        info = default_entry()
        try:
            r = utils.FileParser(filename).parse()
            info.update(**r.getepdata())

            if info['episode'] is None:
                info['failed'] = True
                info['failure_reason'] = 'Could not parse episode number'

            elif info['seriesname'] is None:
                info['failed'] = True
                info['failure_reason'] = 'Could not parse series name'

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

@filtered
def identify(info):
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

    return info

@filtered
def generate_names(info):
    try:
        title = info['identified_as']
        extension = info['ext']

        info['offset'] = shows[title]['offset']
        episode = int(info['episode']) + info['offset']
        info['season'] = int(shows[title]['season'])

        info["new_name"] = utils.makeValidFilename(
            "{identified_as} - S{season}E{episode}{ext}".format(**info),
        )

    except Exception as e:
        info['failed'] = True
        info['failure_reason'] = "Error during name generation ({})".format(e)

    return info

def move_files(infos, clean=True):
    # Note: not passed with filter because of the progress bar

    success = list(filter(lambda s: not s['failed'], infos))

    to_chown = []

    with click.progressbar(length=len(success)) as bar:
        for info in success:
            try:
                new_path = os.path.join(
                    config['destination'],
                    info['identified_as'],
                )

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

            finally:
                bar.update(1)

        return to_chown

def print_status(episodes):
    success = list(filter(lambda i: not i['failed'], episodes))
    failure = list(filter(lambda i: i['failed'], episodes))

    if len(success) == 0 and len(failure) == 0:
        click.secho("No files found.")
        sys.exit(1)

    if len(success) > 0:
        click.secho("Successfully identified:")
        for info in success:
            fname = os.path.basename(info['filename'])
            click.secho("    {} -> {}".format(fname, info['new_name']))

        if len(failure) > 0:
            click.secho("")

    if len(failure) > 0:
        click.secho("Failures:")
        for info in failure:
            fname = os.path.basename(info['filename'])
            click.secho("    {} -> {}".format(fname, info['failure_reason']))

if __name__ == '__main__':
    sort()
