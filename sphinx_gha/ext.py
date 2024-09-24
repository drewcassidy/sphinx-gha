import os
import typing as ty
from pathlib import Path

import yaml
from docutils import nodes
from docutils.parsers.rst import directives
from sphinx import application
from sphinx.util.docutils import SphinxDirective

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
            inputs_section = nodes.section(
                '',
                nodes.title(text='Inputs'),
                ids=[nodes.make_id(action_path.parent.name + '_inputs')],
                names=[nodes.fully_normalize_name('inputs')],
            )

            for input_name, input_meta in action_yaml['inputs'].items():
                confval = [f'.. confval:: {input_name}']
                for meta in ['default', 'type']:
                    if meta in input_meta:
                        confval.append(indent(f':{meta}: {input_meta[meta]}'))
                if 'description' in input_meta:
                    confval.append('')
                    confval.append(indent(input_meta['description']))

                inputs_section.extend(self.parse_text_to_nodes('\n'.join(confval)))
            section.append(inputs_section)

        if 'outputs' in action_yaml:
            outputs_section = nodes.section(
                '',
                nodes.title(text='Outputs'),
                ids=[nodes.make_id(action_path.parent.name + '_outputs')],
                names=[nodes.fully_normalize_name('outputs')],
            )
            for output_name, output_meta in action_yaml['outputs'].items():
                confval = [f'.. confval:: {output_name}']

                if 'description' in output_meta:
                    confval.append('')
                    confval.append(indent(output_meta['description']))

                outputs_section.extend(self.parse_text_to_nodes('\n'.join(confval)))
            section.append(outputs_section)
        return [section]


def setup(app: application.Sphinx) -> ty.Dict[str, ty.Any]:
    app.add_directive("gh-action", ActionDirective)
    app.add_config_value('sphinx_gha_repo_tag', os.environ.get('READTHEDOCS_GIT_IDENTIFIER'), 'env')
    app.add_config_value('sphinx_gha_repo_slug', 'UNKNOWN REPO', 'env')
    app.add_config_value('sphinx_gha_repo_root', os.getcwd(), 'env')

    return {
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
