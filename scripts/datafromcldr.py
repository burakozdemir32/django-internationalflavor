import argparse
import json
import os
import zipfile
import django
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import translation
import polib

from _common import COUNTRY_ALTERNATIVE_KEYS, get_tz_info, update, get_from_path, TIMEZONE_TERRITORY_KEYS, get_language

# This is almost a management command, but we do not want it to be added to the django-admin namespace for the simple
# reason that it is not expected to be executed by package users, only by the package maintainers.
# We use a thin __main__ wrapper to make it work (ish) like a management command.

MODULE_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'internationalflavor')
LOCALE_PATH = os.path.join(MODULE_PATH, 'locale')
LANGUAGES = {}


def translate(language, original, translated):
    entry = polib.POEntry()
    entry.msgid = original
    entry.msgstr = translated
    entry.comment = "auto-generated from CLDR -- see docs before updating"

    if entry not in LANGUAGES[language]:
        LANGUAGES[language].append(entry)


class Command(BaseCommand):
    help = ('Updates localized data of the internationalflavor module using data from the Unicode '
            'Common Locale Data Repository (CLDR)')

    def handle(self, *args, **options):
        translation.deactivate_all()

        try:
            cldr_path = os.path.abspath(options['path_to_cldr'])
            self.stdout.write("Reading CLDR from %s" % cldr_path)

            # cldr-localenames-full  cldr-dates-full cldr-core

            # Load base data
            with open(os.path.join(cldr_path, "cldr-localenames-full", "main", "en", "territories.json"), 'rb') as f:
                data = json.loads(f.read().decode("utf8"))
            with open(os.path.join(cldr_path, "cldr-dates-full", "main", "en", "timeZoneNames.json"), 'rb') as f:
                update(data, json.loads(f.read().decode("utf8")))
            with open(os.path.join(cldr_path, "cldr-core", "supplemental", "metaZones.json"), 'rb') as f:
                update(data, json.loads(f.read().decode("utf8")))

            # Load localized data
            for lc, language in settings.LANGUAGES:
                # Do not load data for languages that are not in my locale directory
                if not os.path.exists(os.path.join(LOCALE_PATH, lc)):
                    continue

                # Always create a PO file for languages that are in my locale directory
                LANGUAGES[lc] = polib.POFile()
                try:
                    cldr_lc = get_language(lc)
                    with open(os.path.join(cldr_path, "cldr-localenames-full", "main", cldr_lc, "territories.json"), 'rb') as f:
                        update(data, json.loads(f.read().decode("utf8")))
                    with open(os.path.join(cldr_path, "cldr-dates-full", "main", cldr_lc, "timeZoneNames.json"), 'rb') as f:
                        update(data, json.loads(f.read().decode("utf8")))
                except Exception as e:
                    self.stderr.write("Language %s will not be translated: %s" % (language, e))

            # Handle territories
            # ------------------
            self.stdout.write("Parsing territory information")
            territories = data['main']['en']['localeDisplayNames']['territories']

            # Write territory info to a file
            with open(os.path.join(MODULE_PATH, "countries", "_cldr_data.py"), 'wb') as f:
                f.write(b"# coding=utf-8\n")
                f.write(b"# This file is automatically generated based on the English CLDR file.\n")
                f.write(b"# Do not edit manually.\n\n")
                f.write(b"from __future__ import unicode_literals\n")
                f.write(b"from django.utils.translation import ugettext_lazy as _\n\n")

                f.write(b"COUNTRY_NAMES = {\n")
                # Loop over each territory
                for territory, name in sorted(territories.items()):
                    # Skip territories that are alternative names
                    if len(territory) > 2 and not territory.isdigit():
                        continue
                    # If we want the alternative name, we get one
                    if territory in COUNTRY_ALTERNATIVE_KEYS:
                        name = territories[COUNTRY_ALTERNATIVE_KEYS[territory]]
                    f.write(b'    "' + territory.encode('utf8') + b'": _("' + name.encode('utf8') + b'"),\n')

                    # Handle translations
                    for lc in LANGUAGES:
                        cldr_lc = get_language(lc)
                        if cldr_lc in data['main']:
                            ldata = data['main'][cldr_lc]['localeDisplayNames']['territories']
                            # We check if the alternative name has some useful translation
                            if territory in COUNTRY_ALTERNATIVE_KEYS and \
                                    COUNTRY_ALTERNATIVE_KEYS[territory] in ldata and \
                                    ldata[COUNTRY_ALTERNATIVE_KEYS[territory]] != territory:
                                translate(lc, name, ldata[COUNTRY_ALTERNATIVE_KEYS[territory]])
                            # If no alternative name, we get a useful translation from the actual name
                            elif territory in ldata and ldata[territory] != territory:
                                translate(lc, name, ldata[territory])
                            # Else just do not translate
                            else:
                                translate(lc, name, '')
                        else:
                            translate(lc, name, '')

                f.write(b"}\n")

            #
            # Handle timezones and metazones
            # ------------------------------
            self.stdout.write("Parsing timezone information")

            tz_locale_data = data['main']['en']['dates']['timeZoneNames']
            timezones = get_tz_info(tz_locale_data['zone'])
            metazones = data['main']['en']['dates']['timeZoneNames']['metazone']
            metazone_info = data['supplemental']['metaZones']['metazoneInfo']['timezone']
            metazone_minfo = data['supplemental']['metaZones']['metazones']

            with open(os.path.join(MODULE_PATH, "timezone", "_cldr_data.py"), 'wb') as f:
                f.write(b"# coding=utf-8\n")
                f.write(b"# This file is automatically generated based on the English CLDR file.\n")
                f.write(b"# Do not edit manually.\n\n")
                f.write(b"from __future__ import unicode_literals\n")
                f.write(b"from django.utils.translation import ugettext_lazy as _\n\n")

                f.write(b"METAZONE_MAPPING_FROM_TZ = {\n")
                # We build a map of timezones to metazones
                for region_path in sorted(timezones):
                    # Get the metazone info
                    mzone = None
                    try:
                        # The mzone that has no end date is valid
                        mzoneinfo = get_from_path(metazone_info, region_path.split('/'))
                        for u in mzoneinfo:
                            if '_to' not in u['usesMetazone']:
                                mzone = u['usesMetazone']['_mzone']
                    except KeyError:
                        pass

                    if mzone is None:
                        f.write(b'    "' + region_path.encode('utf8') + b'": None,\n')
                    else:
                        f.write(b'    "' + region_path.encode('utf8') + b'": "' + mzone.encode('utf8') + b'",\n')

                f.write(b"}\n")

                f.write(b"METAZONE_MAPPING_TO_TZ = {\n")
                for minfo in metazone_minfo:
                    mapzone = minfo['mapZone']
                    f.write(b'    ("' + mapzone['_other'].encode('utf8') + b'", "' + mapzone['_territory'].encode('utf8') +
                            b'"): "' + mapzone['_type'].encode('utf8') + b'",\n')
                f.write(b"}\n")

                f.write(b"TIMEZONE_NAMES = {\n")
                # We now loop over all timezone names
                for region_path, i in sorted(timezones.items()):
                    region_name, city = i
                    # Get the translated region name
                    if region_name in TIMEZONE_TERRITORY_KEYS:
                        region_name = territories[TIMEZONE_TERRITORY_KEYS[region_name]]

                    f.write(b'    "' + region_path.encode('utf8') +
                            b'": (_("' + region_name.encode('utf8') +
                            b'"), _("' + city.encode('utf8') + b'")),\n')

                    # Handle translations, quite simply: we either get the translation, or we don't.
                    # Any translation will suffice here.
                    for lc in LANGUAGES:
                        cldr_lc = get_language(lc)
                        try:
                            translate(lc, city, get_from_path(data['main'][cldr_lc]['dates']['timeZoneNames']['zone'],
                                                              region_path.split('/'))['exemplarCity'])
                        except KeyError:
                            translate(lc, city, '')

                f.write(b"}\n")
                f.write(b"METAZONE_NAMES = {\n")
                # The metazone names are also easy
                for metazone, info in sorted(metazones.items()):
                    # We get the generic name, or if there's none, the standard name
                    if 'generic' in info['long']:
                        name = info['long']['generic']
                    else:
                        name = info['long']['standard']

                    f.write(b'    "' + metazone.encode('utf8') + b'": _("' + name.encode('utf8') + b'"),\n')

                    # Handle translations, any name will suffice.
                    for lc in LANGUAGES:
                        cldr_lc = get_language(lc)
                        try:
                            ldata = data['main'][cldr_lc]['dates']['timeZoneNames']['metazone']
                            if 'generic' in ldata[metazone]['long']:
                                translate(lc, name, ldata[metazone]['long']['generic'])
                            else:
                                translate(lc, name, ldata[metazone]['long']['standard'])
                        except KeyError:
                            translate(lc, name, '')
                f.write(b"}\n")

                f.write(b'TZ_REGION_FORMAT = _("' + tz_locale_data['regionFormat'].replace("{0}", "%s").encode('utf8') + b'")\n')
                f.write(b'TZ_HOUR_FORMAT = _("' + tz_locale_data['hourFormat'].encode('utf8') + b'")\n')
                f.write(b'TZ_GMT_FORMAT = _("' + tz_locale_data['gmtFormat'].replace("{0}", "%s").encode('utf8') + b'")\n')
                for lc in LANGUAGES:
                    try:
                        ldata = data['main'][get_language(lc)]['dates']['timeZoneNames']
                    except KeyError:
                        translate(lc, tz_locale_data['regionFormat'].replace("{0}", "%s"), '')
                        translate(lc, tz_locale_data['hourFormat'], '')
                        translate(lc, tz_locale_data['gmtFormat'].replace("{0}", "%s"), '')
                    else:
                        translate(lc, tz_locale_data['regionFormat'].replace("{0}", "%s"), ldata['regionFormat'].replace("{0}", "%s"))
                        translate(lc, tz_locale_data['hourFormat'], ldata['hourFormat'])
                        translate(lc, tz_locale_data['gmtFormat'].replace("{0}", "%s"), ldata['gmtFormat'].replace("{0}", "%s"))

        except OSError as e:
            raise CommandError("Error while reading CLDR files: %s" % e)

        self.stdout.write("Writing CLDR language file")
        for lc, pofile in LANGUAGES.items():
            pofile.save(os.path.join(LOCALE_PATH, lc, 'LC_MESSAGES', 'cldr.po'))


if __name__ == '__main__':
    settings.configure()
    if hasattr(django, 'setup'):
        django.setup()

    # We parse arguments ourselves. Django 1.8 uses argparse (finally) but we can just as easily use it ourselves.
    parser = argparse.ArgumentParser(description=Command.help)
    parser.add_argument('path_to_cldr', help='Path to a zip file containing CLDR JSON files.')
    args = parser.parse_args()

    Command().execute(**vars(args))