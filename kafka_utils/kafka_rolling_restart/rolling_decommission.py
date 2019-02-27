# -*- coding: utf-8 -*-
# Copyright 2019 Yelp Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from .util import execute_task
from .util import wait_for_stable_cluster
from kafka_utils.util.ssh import ssh


def decommission_broker(host, connection, stop_command, verbose):
    """Execute the stop"""
    pass


def execute_rolling_decommission(
    brokers,
    jolokia_port,
    jolokia_prefix,
    check_interval,
    check_count,
    unhealthy_time_limit,
    skip,
    verbose,
    pre_stop_task,
    post_stop_task,
    start_command,
    stop_command,
    ssh_password=None
):
    """Execute the rolling decommission on the specified brokers. It checks the
    number of under replicated partitions on each broker, using Jolokia.

    The check is performed at constant intervals, and a broker will be decommissioned
    when all the brokers are answering and are reporting zero under replicated
    partitions.

    :param brokers: the brokers that will be decommissioned
    :type brokers: map of broker ids and host names
    :param jolokia_port: HTTP port for Jolokia
    :type jolokia_port: integer
    :param jolokia_prefix: HTTP prefix on the server for the Jolokia queries
    :type jolokia_prefix: string
    :param check_interval: the number of seconds it will wait between each check
    :type check_interval: integer
    :param check_count: the number of times the check should be positive before
    decommissioning the next broker
    :type check_count: integer
    :param unhealthy_time_limit: the maximum number of seconds it will wait for
    the cluster to become stable before exiting with error
    :type unhealthy_time_limit: integer
    :param skip: the number of brokers to skip
    :type skip: integer
    :param verbose: print commend execution information
    :type verbose: bool
    :param pre_stop_task: a list of tasks to execute before running stop
    :type pre_stop_task: list
    :param post_stop_task: a list of task to execute after running stop
    :type post_stop_task: list
    :param start_command: the start command for kafka
    :type start_command: string
    :param stop_command: the stop command for kafka
    :type stop_command: string
    :param ssh_password: The ssh password to use if needed
    :type ssh_password: string
    """
    all_hosts = [b[1] for b in brokers]
    for n, host in enumerate(all_hosts[skip:]):
        with ssh(host=host, forward_agent=True, sudoable=True, max_attempts=3, max_timeout=2,
                 ssh_password=ssh_password) as connection:
            execute_task(pre_stop_task, host)
            wait_for_stable_cluster(
                all_hosts,
                jolokia_port,
                jolokia_prefix,
                check_interval,
                1 if n == 0 else check_count,
                unhealthy_time_limit,
            )
            print("Stopping {0} ({1}/{2})".format(host, n + 1, len(all_hosts) - skip))
            decommission_broker(host, connection, stop_command, verbose)
            execute_task(post_stop_task, host)
    # Wait before terminating the script
    wait_for_stable_cluster(
        all_hosts,
        jolokia_port,
        jolokia_prefix,
        check_interval,
        check_count,
        unhealthy_time_limit,
    )


def add_subparser(subparsers):
    parser_rolling_decommission = subparsers.add_parser(
        "rolling_decommission",
        description="Decommission each broker in the Kafka cluster.",
        add_help=False,
    )
    parser_rolling_decommission.add_argument(
        "-h",
        "--help",
        action="help",
        help="This subcommand executes a rolling decommission on the cluster.",
    )
    parser_rolling_decommission.set_defaults(command=execute_rolling_decommission)
