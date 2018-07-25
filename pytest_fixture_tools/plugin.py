"""Pytest fixture tools plugin."""

import py
import os
import errno
import inspect
import sys
#import pprint
import functools

from _pytest.python import getlocation
from collections import defaultdict

import pydot

tw = py.io.TerminalWriter()
verbose = 1


def mkdir_recursive(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise


def pytest_addoption(parser):
    """Add commandline options show-fixture-duplicates and fixture."""
    group = parser.getgroup("general")
    group.addoption('--show-fixture-duplicates',
                    action="store_true", dest="show_fixture_duplicates", default=False,
                    help="show list of duplicates from available fixtures")
    group.addoption('--fixture',
                    action="store", type=str, dest="fixture_name", default='',
                    help="Name of specific fixture for which you want to get duplicates")

    group.addoption('--fixture-graph',
                    action="store_true", dest="fixture_graph", default=False,
                    help="create .dot fixture graph for each test")
    group.addoption('--fixture-graph-output-dir',
                    action="store_true", dest="fixture_graph_output_dir", default="artifacts",
                    help="select the location for the output of fixture graph. defaults to 'artifacts'")
    group.addoption('--fixture-graph-output-type',
                    action="store_true", dest="fixture_graph_output_type", default="png",
                    help="select the type of the output for the fixture graph. defaults to 'png'")


def pytest_cmdline_main(config):
    """Check show_fixture_duplicates option to show fixture duplicates."""
    if config.option.show_fixture_duplicates:
        show_fixture_duplicates(config)
        return 0


def show_fixture_duplicates(config):
    """Wrap pytest session to show duplicates."""
    from _pytest.main import wrap_session
    return wrap_session(config, _show_fixture_duplicates_main)


def print_duplicates(argname, fixtures, previous_argname):
    """Print duplicates with TerminalWriter."""
    if len(fixtures) > 1:
        fixtures = sorted(fixtures, key=lambda key: key[2])

        for baseid, module, bestrel, fixturedef in fixtures:

            if previous_argname != argname:
                tw.line()
                tw.sep("-", argname)
                previous_argname = argname

            if verbose <= 0 and argname[0] == "_":
                continue

            funcargspec = bestrel

            tw.line(funcargspec)


def _show_fixture_duplicates_main(config, session):
    """Preparing fixture duplicates for output."""
    session.perform_collect()
    curdir = py.path.local()

    fm = session._fixturemanager

    fixture_name = config.option.fixture_name
    available = defaultdict(list)
    arg2fixturedefs = ([fixture_name]
                       if fixture_name and fixture_name in fm._arg2fixturedefs
                       else fm._arg2fixturedefs)
    for item in session.items:
        for argname in arg2fixturedefs:
            fixturedefs = fm.getfixturedefs(argname, item.nodeid)
            assert fixturedefs is not None
            if not fixturedefs:
                continue

            for fixturedef in fixturedefs:
                loc = getlocation(fixturedef.func, curdir)

                fixture = (
                    len(fixturedef.baseid),
                    fixturedef.func.__module__,
                    curdir.bestrelpath(loc),
                    fixturedef
                )
                if fixture[2] not in [f[2] for f in available[argname]]:
                    available[argname].append(fixture)

    if fixture_name:
        print_duplicates(fixture_name, available[fixture_name], None)
    else:
        available = sorted([(key, items) for key, items in available.items()], key=lambda key: key[0])

        previous_argname = None
        for argname, fixtures in available:
            print_duplicates(argname, fixtures, previous_argname)
            previous_argname = argname


def pytest_collection_modifyitems(session, config, items):
    if config.option.fixture_graph:
        save_fixture_graph(
            config,
            name2fixturedefs={
                name: [
                    fixture_def for fixture_def in fixture_defs
                    # Exclude fixtures defined in test modules.
                    if not fixture_def.func.__module__.split('.')[-1].startswith("test_")
                ]
                for name, fixture_defs in session._fixturemanager._arg2fixturedefs.items()
            },
            filename='fixture-graph',
        )


def pytest_runtest_setup(item):
    if item.config.option.fixture_graph and hasattr(item, "_fixtureinfo"):
        save_fixture_graph(
            item.config, item._fixtureinfo.name2fixturedefs,
            filename="fixture-graph-{}".format(item._nodeid.replace(":", "_").replace("/", "-")),
            func_args=item._fixtureinfo.argnames,
        )


def _get_fixture_search_order(func_path):
    func_dir = os.path.dirname(func_path)
    if func_dir == func_path:
        return []
    conftest_path = os.path.join(func_dir, 'conftest.py')
    return [conftest_path] + _get_fixture_search_order(func_dir)


def _get_func_path(func, path_cache):
    try:
        return path_cache[func]
    except KeyError:
        path_cache[func] = func_path = inspect.getfile(func)
        return func_path


def _find_fixture_def(source_fixture_name, func_path, fixture_name, name2fixturedefs, get_func_path):
    search_order = _get_fixture_search_order(os.path.dirname(func_path))

    if source_fixture_name != fixture_name:
        # Do not include same file in search path when overriding a fixture.
        search_order.insert(0, func_path)

    def sort_key(fixture_def):
        try:
            return search_order.index(get_func_path(fixture_def.func))
        except ValueError:
            return sys.maxsize
    try:
        target_fixture_defs = name2fixturedefs[fixture_name]
    except KeyError:
        return None
    try:
        fixture_def = sorted(
            target_fixture_defs,
            key=sort_key,
        )[0]
    except IndexError:
        return None
    return fixture_def


def _get_cluster_name(func_path, cwd):
    return os.path.relpath(func_path, cwd) if func_path.startswith(cwd) else func_path


def _get_fixture_node_name(fixture_name, fixture_def, cwd, get_func_path):
    if fixture_def is None:
        return '', ''
    func_path = get_func_path(fixture_def.func)
    return _get_cluster_name(func_path, cwd), fixture_name


def save_fixture_graph(config, name2fixturedefs, filename, func_args=None):
    data = defaultdict(dict)
    if func_args:
        data['']['func_args'] = func_args, 'red'
    cwd = os.getcwd() + os.sep

    get_func_path = functools.partial(_get_func_path, path_cache={})

    for fixture_name, fixture_defs in list(name2fixturedefs.items()):
        if fixture_name == 'request':
            continue

        for fixture_def in fixture_defs:
            func_path = get_func_path(fixture_def.func)
            cluster_name = _get_cluster_name(func_path, cwd)
            color = 'green'
            data[cluster_name][fixture_name] = [
                _get_fixture_node_name(
                    argname, _find_fixture_def(
                        fixture_name, func_path, argname, name2fixturedefs, get_func_path,
                    ), cwd, get_func_path,
                )
                for argname in fixture_def.argnames
            ], color

    #print(pprint.pformat(dict(data)))

    graph = pydot.Dot(graph_type='digraph')
    #graph.set_splines('true')
    graph.set_concentrate('true')
    graph.set_rankdir('LR')
    #graph.set_overlap('compress')
    #graph.set_ratio('compress')

    for func_path, subgraph_data in data.items():

        subgraph = pydot.Cluster(graph_name=func_path)
        #subgraph.set_splines('true')
        subgraph.set_label(func_path)
        subgraph.set_concentrate('true')
        #subgraph.set_overlap('compress')
        #subgraph.set_ratio('compress')
        #subgraph.set_size(1)
        graph.add_subgraph(subgraph)

        for name, depended_list in list(subgraph_data.items()):
            depended_list, color = depended_list

            node = pydot.Node(func_path + "/" + name, style="filled", fillcolor=color)
            node.set_label(name)
            subgraph.add_node(node)
            for dest_cluster, dest_name in depended_list:
                if not dest_name:
                    continue
                edge = pydot.Edge(node, dest_cluster + '/' + dest_name)
                graph.add_edge(edge)
                #subgraph.set_ltail(dest_cluster)

    log_dir = config.option.fixture_graph_output_dir
    output_type = config.option.fixture_graph_output_type
    mkdir_recursive(log_dir)
    filename = os.path.join(log_dir, filename)
    tw.line()
    tw.sep("-", "fixture-graph")
    tw.line("created {}.dot.".format(filename))
    graph.write(filename + ".dot")
    try:
        graph.write("{}.{}".format(filename, output_type), format=output_type)
        tw.line("created {}.{}.".format(filename, output_type))
    except Exception:
        tw.line("graphvis wasn't found in PATH")
        tw.line("You can convert it to a PNG using:\n\t'dot -Tpng {0}.dot -o {0}.png'".format(filename))
