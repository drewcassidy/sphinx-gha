import os
import typing as ty
from pathlib import Path

import yaml
from docutils import nodes
from docutils.nodes import Element
from docutils.parsers.rst import directives
from myst_parser.mdit_to_docutils.base import DocutilsRenderer
from myst_parser.mdit_to_docutils.sphinx_ import SphinxRenderer
from myst_parser.mocking import MockInliner
from myst_parser.parsers.mdit import create_md_parser
from myst_parser.parsers.sphinx_ import MystParser
from sphinx import application
from sphinx.domains.std import ConfigurationValue
from sphinx.util.docutils import SphinxDirective, new_document

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper


def indent(text: str, level=1):
    lines = text.splitlines()
    return '\n'.join([(' ' * level) + line for line in lines])


class MarkdownParsingMixin(Element):
    def __init__(self, rawsource: str = "", *children, **attributes):
        super().__init__(rawsource, *children, **attributes)

        self.md_parser = None
        self.md_renderer = None

    def parse_markdown_inline(self, markdown):
        if not hasattr(self, 'md_renderer'):
            config = self.state.document.settings.env.myst_config
            self.md_parser = create_md_parser(config, SphinxRenderer)
            self.md_renderer = DocutilsRenderer(self.md_parser)
            self.md_renderer.setup_render({"myst_config": config}, {})
        inliner = MockInliner(self.md_renderer)
        return inliner.parse(markdown, self.lineno, None, None)[0]


class ActionInputDirective(ConfigurationValue):
    option_spec = ConfigurationValue.option_spec | {
        'required': directives.unchanged_required,
        'deprecationMessage': directives.unchanged_required
    }

    def format_deprecationMessage(self, message):
        document = new_document('', self.state.document.settings)
        parser = MystParser()
        parser.parse(message, document)
        parsed = document.children

    def format_required(self, required: str):
        """Formats the ``:type:`` option."""
        parsed, msgs = self.parse_inline(required, lineno=self.lineno)
        field = nodes.field(
            '',
            nodes.field_name('', 'Required'),
            nodes.field_body('', *parsed),
        )
        return field, msgs

    def transform_content(self, content_node) -> None:
        field_list = nodes.field_list()
        if 'deprecationMessage' in self.options:
            field, msgs = self.format_default(self.options['default'])
            field_list.append(field)
            field_list += msgs
        if 'required' in self.options:
            field, msgs = self.format_type(self.options['required'])
            field_list.append(field)
            field_list += msgs
        if len(field_list.children) > 0:
            content_node.insert(0, field_list)

        super(ActionInputDirective, self).transform_content(content_node)


class ActionDirective(SphinxDirective, MarkdownParsingMixin):
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
                    item_nodes[1][1].extend(self.parse_markdown_inline(item_meta['description']))
                value_section.extend(item_nodes)
            section.append(value_section)

        if 'description' in action_yaml:
            section.extend(self.parse_markdown_inline(action_yaml['description']))

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

        # foo = ConfigurationValue('confval', ['butts'], {'type': 'butts'}, 'this is butts', self.lineno, self.content_offset, "", self.state, self.state_machine)
        # section.extend(foo.run())

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
