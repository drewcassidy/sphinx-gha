import sphinx.directives
import typing as ty
from pathlib import Path

from docutils import nodes
from docutils.parsers import rst
from docutils.parsers.rst import directives
from docutils import statemachine
from sphinx import application
from sphinx.util import logging, nested_parse_with_titles
from sphinx.util import nodes as sphinx_nodes
from sphinx.ext.autodoc import mock

import yaml
from sphinx.util.docutils import sphinx_domains, SphinxDirective

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper


def indent(text: str, level=1):
    lines = text.splitlines()
    return '\n'.join([(' ' * level) + line for line in lines])


class ActionDirective(SphinxDirective):
    has_content = True
    required_arguments = 0
    option_spec = {
        'path': directives.unchanged_required,
    }

    def run(self):
        action_path = Path(self.options['path'])

        with open(action_path, 'rt') as stream:
            action_yaml = yaml.full_load(stream)

        # Title

        section: nodes.Element = nodes.section(
            '',
            nodes.title(text=action_yaml['name']),
            ids=[nodes.make_id(action_path.parent.name)],
            names=[nodes.fully_normalize_name(action_path.parent.name)],
        )

        if 'description' in action_yaml:
            section.append(nodes.paragraph('', action_yaml['description']))

        if 'inputs' in action_yaml:
            inputs_contents = statemachine.StringList()
            inputs_section = nodes.section(
                '',
                nodes.title(text='Inputs'),
                ids=[nodes.make_id(action_path.parent.name + '_inputs')],
                names=[nodes.fully_normalize_name('inputs')],
            )
            for input_name, input_meta in action_yaml['inputs'].items():
                inputs_contents.append(f'.. confval:: {input_name}', action_path)
                if 'default' in input_meta:
                    inputs_contents.append(indent(f':default: {input_meta["default"]}'), action_path)
                if 'type' in input_meta:
                    inputs_contents.append(indent(f':type: {input_meta["type"]}'), action_path)
                if 'description' in input_meta:
                    inputs_contents.append('', action_path)
                    inputs_contents.append(indent(input_meta['description']), action_path)

            nested_parse_with_titles(self.state, inputs_contents, inputs_section)
            section.append(inputs_section)

        if 'outputs' in action_yaml:
            outputs_contents = statemachine.StringList()
            outputs_section = nodes.section(
                '',
                nodes.title(text='Outputs'),
                ids=[nodes.make_id(action_path.parent.name + '_outputs')],
                names=[nodes.fully_normalize_name('outputs')],
            )
            for input_name, input_meta in action_yaml['outputs'].items():
                outputs_contents.append(f'.. confval:: {input_name}', action_path)
                if 'description' in input_meta:
                    outputs_contents.append('', action_path)
                    outputs_contents.append(indent(input_meta['description']), action_path)

            nested_parse_with_titles(self.state, outputs_contents, outputs_section)
            section.append(outputs_section)

        nested_parse_with_titles(self.state, self.content, section)

        return [section]


def setup(app: application.Sphinx) -> ty.Dict[str, ty.Any]:
    app.add_directive("gh-action", ActionDirective)

    return {
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
