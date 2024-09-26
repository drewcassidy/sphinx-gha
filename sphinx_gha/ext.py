import os
import typing as ty
from pathlib import Path

import yaml
from docutils import nodes
from docutils.nodes import Element
from docutils.parsers.rst import directives, Directive
from myst_parser.mdit_to_docutils.base import DocutilsRenderer
from myst_parser.mdit_to_docutils.sphinx_ import SphinxRenderer
from myst_parser.mocking import MockInliner, MockStateMachine, MockState
from myst_parser.parsers.mdit import create_md_parser
from myst_parser.parsers.sphinx_ import MystParser
from sphinx import application
from sphinx.addnodes import versionmodified
from sphinx.domains.std import ConfigurationValue
from sphinx.util.docutils import SphinxDirective, new_document

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper


def indent(text: str, level=1):
    lines = text.splitlines()
    return '\n'.join([(' ' * level) + line for line in lines])


class MarkdownParsingMixin(Directive):
    @property
    def md_renderer(self):
        if not hasattr(self, '_md_renderer'):
            config = self.state.document.settings.env.myst_config
            self._md_parser = create_md_parser(config, SphinxRenderer)
            self._md_renderer = DocutilsRenderer(self._md_parser)
            self._md_renderer.setup_render({"myst_config": config}, {})
        return self._md_renderer

    def parse_markdown(self, markdown, inline=False, node=None):
        renderer = self.md_renderer
        renderer.current_node = node or Element('')
        renderer.nested_render_text(markdown, self.lineno, inline=inline)
        return renderer.current_node.children


class ActionItemDirective(ConfigurationValue, MarkdownParsingMixin):
    _name = 'gh-action-item'
    option_spec = ConfigurationValue.option_spec | {
        'required': directives.unchanged_required,
        'deprecationMessage': directives.unchanged_required
    }

    @classmethod
    def generate(cls, item_name, item_meta, lineno, content_offset, state, state_machine):
        options = {k: str(v) for k, v in item_meta.items() if k in cls.option_spec}
        directive = cls('confval', [item_name], options, '', lineno, content_offset, "", state, state_machine)
        node = directive.run()
        if 'description' in item_meta:
            directive.parse_markdown(item_meta['description'], node=node[1][1])
        return node

    def format_deprecationMessage(self, message):
        admonition = versionmodified()
        admonition['type'] = 'deprecated'
        admonition.document = self.state.document
        self.parse_markdown(message, inline=True, node=admonition)
        return admonition, []

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
            field, msgs = self.format_required(self.options['required'])
            field_list.append(field)
            field_list += msgs
        if len(field_list.children) > 0:
            content_node.insert(0, field_list)

        super(ActionItemDirective, self).transform_content(content_node)


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
                value_section.extend(ActionItemDirective.generate(item_name, item_meta, self.lineno, self.content_offset, self.state, self.state_machine))
                # item_rst = [f'.. {directive} :: {item_name}']
                # for meta_tag in metas:
                #     if meta_tag in item_meta:
                #         item_rst.append(indent(f':{meta_tag}: {item_meta[meta_tag]}'))
                # item_nodes = self.parse_text_to_nodes('\n'.join(item_rst))
                # if 'description' in item_meta:
                #     item_nodes[1][1].extend(self.parse_markdown(item_meta['description']))
                # value_section.extend(item_nodes)
            section.append(value_section)

        if 'description' in action_yaml:
            section.extend(self.parse_markdown(action_yaml['description']))

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
    app.add_directive('gh-action-item', ActionItemDirective)
    app.add_config_value('sphinx_gha_repo_tag', os.environ.get('READTHEDOCS_GIT_IDENTIFIER'), 'env')
    app.add_config_value('sphinx_gha_repo_slug', 'UNKNOWN REPO', 'env')
    app.add_config_value('sphinx_gha_repo_root', os.getcwd(), 'env')
    app.setup_extension('myst_parser')

    return {
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
