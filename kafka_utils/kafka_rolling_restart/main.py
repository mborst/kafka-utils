# -*- coding: utf-8 -*-
# Copyright 2016 Yelp Inc.
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

import argparse
import logging
import sys

from six.moves import input
from six.moves import zip

from .error import WaitTimeoutException
from .task import PostStopTask
from .task import PreStopTask
from .task import TaskFailedException
from .util import filter_broker_list
from .util import get_broker_list
from kafka_utils.util import config
from kafka_utils.util.utils import dynamic_import


DEFAULT_CHECK_INTERVAL_SECS = 10
DEFAULT_CHECK_COUNT = 12
DEFAULT_TIME_LIMIT_SECS = 600
DEFAULT_JOLOKIA_PORT = 8778
DEFAULT_JOLOKIA_PREFIX = "jolokia/"
DEFAULT_STOP_COMMAND = "service kafka stop"
DEFAULT_START_COMMAND = "service kafka start"


def parse_opts():
    parser = argparse.ArgumentParser(
        description=('Performs a rolling restart of the specified '
                     'kafka cluster.'))
    parser.add_argument(
        '--cluster-type',
        '-t',
        required=True,
        help='cluster type, e.g. "standard"',
    )
    parser.add_argument(
        '--cluster-name',
        '-c',
        help='cluster name, e.g. "uswest1-devc" (defaults to local cluster)',
    )
    parser.add_argument(
        '--broker-ids',
        '-b',
        required=False,
        type=int,
        nargs='+',
        help='space separated broker IDs to restart (optional, will restart all Kafka brokers in cluster if not specified)',
    )
    parser.add_argument(
        '--discovery-base-path',
        dest='discovery_base_path',
        type=str,
        help='Path of the directory containing the <cluster_type>.yaml config',
    )
    parser.add_argument(
        '--check-interval',
        help=('the interval between each check, in seconds. '
              'Default: %(default)s seconds'),
        type=int,
        default=DEFAULT_CHECK_INTERVAL_SECS,
    )
    parser.add_argument(
        '--check-count',
        help=('the minimum number of times the cluster should result stable '
              'before restarting the next broker. Default: %(default)s'),
        type=int,
        default=DEFAULT_CHECK_COUNT,
    )
    parser.add_argument(
        '--unhealthy-time-limit',
        help=('the maximum amount of time the cluster can be unhealthy before '
              'stopping the rolling restart. Default: %(default)s'),
        type=int,
        default=DEFAULT_TIME_LIMIT_SECS,
    )
    parser.add_argument(
        '--jolokia-port',
        help='the jolokia port on the server. Default: %(default)s',
        type=int,
        default=DEFAULT_JOLOKIA_PORT,
    )
    parser.add_argument(
        '--jolokia-prefix',
        help='the jolokia HTTP prefix. Default: %(default)s',
        type=str,
        default=DEFAULT_JOLOKIA_PREFIX,
    )
    parser.add_argument(
        '--no-confirm',
        help='proceed without asking confirmation. Default: %(default)s',
        action="store_true",
    )
    parser.add_argument(
        '--skip',
        help=('the number of brokers to skip without restarting. Brokers are '
              'restarted in increasing broker-id order. Default: %(default)s'),
        type=int,
        default=0,
    )
    parser.add_argument(
        '-v',
        '--verbose',
        help='print verbose execution information. Default: %(default)s',
        action="store_true",
    )
    parser.add_argument(
        '--task',
        type=str,
        action='append',
        help='Module containing an implementation of Task.'
        'The module should be specified as path_to_include_to_py_path. '
        'ex. --task kafka_utils.kafka_rolling_restart.version_precheck'
    )
    parser.add_argument(
        '--task-args',
        type=str,
        action='append',
        help='Arguements which are needed by the task(prestoptask or poststoptask).'
    )
    parser.add_argument(
        '--start-command',
        type=str,
        help=('Override start command for kafka (do not include sudo)'
              'Default: %(default)s'),
        default=DEFAULT_START_COMMAND,
    )
    parser.add_argument(
        '--stop-command',
        type=str,
        help=('Override stop command for kafka (do not include sudo)'
              'Default: %(default)s'),
        default=DEFAULT_STOP_COMMAND,
    )
    parser.add_argument(
        '--ssh-password',
        type=str,
        help=('SSH passowrd to use if needed'),
    )
    return parser.parse_args()


def print_brokers(cluster_config, brokers):
    """Print the list of brokers that will be restarted.

    :param cluster_config: the cluster configuration
    :type cluster_config: map
    :param brokers: the brokers that will be restarted
    :type brokers: map of broker ids and host names
    """
    print("Will restart the following brokers in {0}:".format(cluster_config.name))
    for id, host in brokers:
        print("  {0}: {1}".format(id, host))


def ask_confirmation():
    """Ask for confirmation to the user. Return true if the user confirmed
    the execution, false otherwise.

    :returns: bool
    """
    while True:
        print("Do you want to restart these brokers? ", end="")
        choice = input().lower()
        if choice in ['yes', 'y']:
            return True
        elif choice in ['no', 'n']:
            return False
        else:
            print("Please respond with 'yes' or 'no'")


def validate_opts(opts, brokers_num):
    """Basic option validation. Returns True if the options are not valid,
    False otherwise.

    :param opts: the command line options
    :type opts: map
    :param brokers_num: the number of brokers
    :type brokers_num: integer
    :returns: bool
    """
    if opts.skip < 0 or opts.skip >= brokers_num:
        print("Error: --skip must be >= 0 and < #brokers")
        return True
    if opts.check_count < 0:
        print("Error: --check-count must be >= 0")
        return True
    if opts.unhealthy_time_limit < 0:
        print("Error: --unhealthy-time-limit must be >= 0")
        return True
    if opts.check_count == 0:
        print("Warning: no check will be performed")
    if opts.check_interval < 0:
        print("Error: --check-interval must be >= 0")
        return True
    return False


def validate_broker_ids_subset(broker_ids, subset_ids):
    """Validate that user specified broker ids to restart exist in the broker ids retrieved
    from cluster config.

    :param broker_ids: all broker IDs in a cluster
    :type broker_ids: list of integers
    :param subset_ids: broker IDs specified by user
    :type subset_ids: list of integers
    :returns: bool
    """
    all_ids = set(broker_ids)
    valid = True
    for subset_id in subset_ids:
        valid = valid and subset_id in all_ids
        if subset_id not in all_ids:
            print("Error: user specified broker id {0} does not exist in cluster.".format(subset_id))
    return valid


def get_task_class(tasks, task_args):
    """Reads in a list of tasks provided by the user,
    loads the appropiate task, and returns two lists,
    pre_stop_tasks and post_stop_tasks
    :param tasks: list of strings locating tasks to load
    :type tasks: list
    :param task_args: list of strings to be used as args
    :type task_args: list
    """
    pre_stop_tasks = []
    post_stop_tasks = []
    task_to_task_args = dict(list(zip(tasks, task_args)))
    tasks_classes = [PreStopTask, PostStopTask]

    for func, task_args in task_to_task_args.items():
        for task_class in tasks_classes:
            imported_class = dynamic_import(func, task_class)
            if imported_class:
                if task_class is PreStopTask:
                    pre_stop_tasks.append(imported_class(task_args))
                elif task_class is PostStopTask:
                    post_stop_tasks.append(imported_class(task_args))
                else:
                    print("ERROR: Class is not a type of Pre/Post StopTask:" + func)
                    sys.exit(1)
    return pre_stop_tasks, post_stop_tasks


def run():
    opts = parse_opts()
    if opts.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARN)
    cluster_config = config.get_cluster_config(
        opts.cluster_type,
        opts.cluster_name,
        opts.discovery_base_path,
    )
    brokers = get_broker_list(cluster_config)
    if opts.broker_ids:
        if not validate_broker_ids_subset([id for id, host in brokers], opts.broker_ids):
            sys.exit(1)
        brokers = filter_broker_list(brokers, opts.broker_ids)
    if validate_opts(opts, len(brokers)):
        sys.exit(1)
    pre_stop_tasks = []
    post_stop_tasks = []
    if opts.task:
        pre_stop_tasks, post_stop_tasks = get_task_class(opts.task, opts.task_args)
    print_brokers(cluster_config, brokers[opts.skip:])
    if opts.no_confirm or ask_confirmation():
        print("Execute rolling command")
        try:
            opts.command(
                brokers,
                opts.jolokia_port,
                opts.jolokia_prefix,
                opts.check_interval,
                opts.check_count,
                opts.unhealthy_time_limit,
                opts.skip,
                opts.verbose,
                pre_stop_tasks,
                post_stop_tasks,
                opts.start_command,
                opts.stop_command,
                opts.ssh_password
            )
        except TaskFailedException:
            print("ERROR: pre/post tasks failed, exiting")
            sys.exit(1)
        except WaitTimeoutException:
            print("ERROR: cluster is still unhealthy, exiting")
            sys.exit(1)
