from __future__ import print_function, absolute_import

import argparse
import json
import re

from ripe.atlas.cousteau import (
    Ping, Traceroute, Dns, Sslcert, Ntp, AtlasSource, AtlasCreateRequest)

from ..exceptions import RipeAtlasToolsException
from ..settings import conf
from ..streaming import Stream
from .base import Command as BaseCommand


class ArgumentType(object):

    @staticmethod
    def country_code(string):
        if not re.match(r"^[a-zA-Z][a-zA-Z]$", string):
            raise argparse.ArgumentTypeError(
                "Countries must be defined with a two-letter ISO code")
        return string.upper()

    @staticmethod
    def probe_listing(string):
        for probe_id in string.split(","):
            if not probe_id.isdigit():
                raise argparse.ArgumentTypeError(
                    "The probe ids supplied were not in the correct format."
                    "Note that you must specify them as a list of "
                    "comma-separated integers without spaces.  Example: "
                    "--from-probes=1,2,34,157,10006"
                )
        return string


class Command(BaseCommand):

    DESCRIPTION = "Create a measurement and optionally wait for the results"

    CREATION_CLASSES = {
        "ping": Ping,
        "traceroute": Traceroute,
        "dns": Dns,
        "ssl": Sslcert,
        "ntp": Ntp
    }

    def add_arguments(self):

        # Required

        self.parser.add_argument(
            "type",
            type=str,
            choices=self.CREATION_CLASSES.keys(),
            help="The type of measurement you want to create"
        )

        # Standard for all types

        self.parser.add_argument(
            "--auth",
            type=str,
            default=conf["authorisation"]["create"],
            help="The API key you want to use to create the measurement"
        )
        self.parser.add_argument(
            "--af",
            type=int,
            default=conf["specification"]["af"],
            choices=(4, 6),
            help="The address family, either 4 or 6"
        )
        self.parser.add_argument(
            "--description",
            type=str,
            default=conf["specification"]["description"],
            help="A free-form description"
        )
        self.parser.add_argument(  # Most types
            "--target",
            type=str,
            help="The target, either a domain name or IP address"
        )
        self.parser.add_argument(
            "--no-report",
            action="store_true",
            help="Don't wait for a response from the measurement, just return "
                 "the URL at which you can later get information about the "
                 "measurement"
        )

        self.parser.add_argument(
            "--interval",
            type=int,
            help="Rather than run this measurement as a one-off (the default), "
                 "create this measurement as a recurring one, with an interval "
                 "of n seconds between attempted measurements. This option "
                 "implies --no-report"
        )

        origins = self.parser.add_mutually_exclusive_group()
        origins.add_argument(
            "--from-area",
            type=str,
            choices=("WW", "West", "North-Central", "South-Central",
                     "North-East", "South-East"),
            help="The area from which you'd like to select your probes."
        )
        origins.add_argument(
            "--from-country",
            type=ArgumentType.country_code,
            metavar="COUNTRY",
            help="The two-letter ISO code for the country from which you'd "
                 "like to select your probes. Example: --from-country=GR"
        )
        origins.add_argument(
            "--from-prefix",
            type=str,
            metavar="PREFIX",
            help="The prefix from which you'd like to select your probes. "
                 "Example: --from-prefix=82.92.0.0/14"
        )
        origins.add_argument(
            "--from-asn",
            type=int,
            metavar="ASN",
            help="The ASN from which you'd like to select your probes. "
                 "Example: --from-asn=3265"
        )
        origins.add_argument(
            "--from-probes",
            type=str,
            metavar="PROBES",
            help="A comma-separated list of probe-ids you want to use in your"
                 "measurement. Example: --from-probes=1,2,34,157,10006"
        )
        origins.add_argument(
            "--from-measurement",
            type=ArgumentType.probe_listing,
            metavar="MEASUREMENT_ID",
            help="A measurement id which you want to use as the basis for probe"
                 "selection in your new measurement.  This is a handy way to"
                 "re-create a measurement under conditions similar to another"
                 "measurement. Example: --from-measurement=1000192"
        )
        self.parser.add_argument(
            "--probes",
            type=int,
            default=conf["specification"]["source"]["requested"],
            help="The number of probes you want to use"
        )

        # Type-specific

        ping_or_trace = self.parser.add_argument_group(
            "Ping and Traceroute Measurements")
        ping_or_trace.add_argument(
            "--packets",
            type=int,
            default=conf["specification"]["types"]["ping"]["packets"],
            help="The number of packets sent"
        )
        ping_or_trace.add_argument(
            "--size",
            type=int,
            default=conf["specification"]["types"]["ping"]["size"],
            help="The size of packets sent"
        )

        traceroute = self.parser.add_argument_group(
            "Traceroute Only Measurements")
        traceroute.add_argument(
            "--protocol",
            type=str,
            default=conf["specification"]["types"]["traceroute"]["protocol"],
            choices=("ICMP", "UDP", "TCP"),
            help="The traceroute protocol used"
        )
        traceroute.add_argument(
            "--timeout",
            type=int,
            default=conf["specification"]["types"]["traceroute"]["timeout"],
            help="The timeout per-packet"
        )

    def run(self):

        creation_class = self.CREATION_CLASSES[self.arguments.type]

        (is_success, response) = AtlasCreateRequest(
            server=conf["ripe-ncc"]["endpoint"].replace("https://", ""),
            key=self.arguments.auth,
            measurements=[creation_class(**self._get_measurement_kwargs())],
            sources=[AtlasSource(**self._get_source_kwargs())],
        ).create()

        if not is_success:
            self._handle_api_error(response)  # Raises an exception

        pk = response["measurements"][0]
        self.ok(
            "Looking good!  Your measurement was created and details about "
            "it can be found here:\n\n  {}/measurements/{}/".format(
                conf["ripe-ncc"]["endpoint"],
                pk
            )
        )

        if not self.arguments.no_report:
            self.ok("Connecting to stream...")
            try:
                Stream.stream(self.arguments.type, pk)
            except KeyboardInterrupt:
                self.ok("Disconnecting from stream")

    def clean_target(self):

        # DNS measurements are a special case for targets
        if self.arguments.type == "dns":
            if self.arguments.use_probes_resolver:
                if self.arguments.target:
                    raise RipeAtlasToolsException(
                        "You may not specify a target for a DNS measurement "
                        "that uses the probe's resolver"
                    )
                return None

        # All other measurement types require it
        if not self.arguments.target:
            raise RipeAtlasToolsException(
                "You must specify a target for that kind of measurement"
            )

        return self.arguments.target

    def _get_measurement_kwargs(self):

        target = self.clean_target()

        spec = conf["specification"]  # Shorter names are easier to read
        r = {
            "af": spec["af"],
            "description": spec["description"],
        }

        if self.arguments.af:
            r["af"] = self.arguments.af
        if self.arguments.description:
            r["description"] = self.arguments.description

        r["is_oneoff"] = True
        if self.arguments.interval or spec["times"]["interval"]:
            r["is_oneoff"] = False
            r["interval"] = self.arguments.interval
            self.arguments.no_report = True
        elif not spec["times"]["one-off"]:
            raise RipeAtlasToolsException(
                "Your configuration file appears to be setup to not create "
                "one-offs, but also offers no interval value.  Without one of "
                "these, we cannot create a measurement."
            )

        if target:
            r["target"] = target

        if self.arguments.type in ("ping", "traceroute"):
            r["packets"] = self.arguments.packets
            r["size"] = self.arguments.size
            if self.arguments.type == "traceroute":
                r["protocol"] = self.arguments.protocol
                r["timeout"] = self.arguments.timeout

        return r

    def _get_source_kwargs(self):

        r = conf["specification"]["source"]
        if self.arguments.from_country:
            r["type"] = "country"
            r["value"] = self.arguments.from_country
        elif self.arguments.from_area:
            r["type"] = "area"
            r["value"] = self.arguments.from_area
        elif self.arguments.from_prefix:
            r["type"] = "prefix"
            r["value"] = self.arguments.from_prefix
        elif self.arguments.from_asn:
            r["type"] = "asn"
            r["value"] = self.arguments.from_asn
        elif self.arguments.from_probes:
            r["type"] = "probes"
            r["value"] = self.arguments.from_probes
        elif self.arguments.from_measurement:
            r["type"] = "msm"
            r["value"] = self.arguments.from_measurement

        return r

    @staticmethod
    def _handle_api_error(response):

        if "HTTP_MSG" in response:

            message = "There was a problem communicating with the RIPE Atlas " \
                      "infrastructure.  The message given was:\n\n  {}".format(
                      response["HTTP_MSG"])

            try:
                message += "\n  " + json.loads(
                    response["ADDITIONAL_MSG"])["error"]["message"]
            except Exception:
                pass  # Too many things can go wrong here and we don't care

            raise RipeAtlasToolsException(message)

        raise RipeAtlasToolsException(
            "There was a problem found in the attempt to create your "
            "measurement.  Please send the command you tried to execute "
            "to atlas@ripe.net and we'll try to address it."
        )
