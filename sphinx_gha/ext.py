import os
import typing as ty
from pathlib import Path

import docutils
import yaml
from docutils import nodes
from docutils.parsers.rst import directives
from myst_parser.mdit_to_docutils.base import DocutilsRenderer
from myst_parser.parsers.sphinx_ import MystParser
from sphinx import application
from sphinx.util.docutils import SphinxDirective, new_document

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

        def document_values(title, items, directive='confval', metas=None):
            if metas is None:
                metas = []
            value_section = nodes.section(
                '',
                nodes.title(text=title),
                ids=[nodes.make_id(action_path.parent.name + '_' + title)],
                names=[nodes.fully_normalize_name(title)],
            )

            for item_name, item_meta in items.items():
                if item_meta is None:
                    item_meta = {}
                item_rst = [f'.. {directive} :: {item_name}']
                for meta_tag in metas:
                    if meta_tag in item_meta:
                        item_rst.append(indent(f':{meta_tag}: {item_meta[meta_tag]}'))
                item_nodes = self.parse_text_to_nodes('\n'.join(item_rst))
                if 'description' in item_meta:
                    # item_rst.append('')
                    # item_rst.append(indent(item_meta['description']))
                    document = new_document(action_path.__str__(), self.state.document.settings)
                    parser = MystParser()
                    parser.parse(indent(item_meta['description']), document)
                    item_nodes[1][1].extend(document.children)
                value_section.extend(item_nodes)
            section.append(value_section)

        if 'description' in action_yaml:
            section.extend(self.parse_text_to_nodes(action_yaml['description']))

        if 'x-env' in action_yaml:
            document_values(
                'Environment Variables',
                action_yaml['x-env'],
                directive='envvar'
            )

        if 'inputs' in action_yaml:
            document_values(
                'Inputs',
                action_yaml['inputs'],
                metas=['default', 'type']
            )

        if 'outputs' in action_yaml:
            document_values(
                'Outputs',
                action_yaml['outputs'],
            )

        return [section]


def setup(app: application.Sphinx) -> ty.Dict[str, ty.Any]:
    app.add_directive("gh-action", ActionDirective)
    app.add_config_value('sphinx_gha_repo_tag', os.environ.get('READTHEDOCS_GIT_IDENTIFIER'), 'env')
    app.add_config_value('sphinx_gha_repo_slug', 'UNKNOWN REPO', 'env')
    app.add_config_value('sphinx_gha_repo_root', os.getcwd(), 'env')
    app.setup_extension('myst_parser')

    return {
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
