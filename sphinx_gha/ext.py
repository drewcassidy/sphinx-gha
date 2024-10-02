import os
import typing as ty
from pathlib import Path
from typing import Iterable

import sphinx
import yaml
from docutils import nodes
from docutils.nodes import Element
from docutils.parsers.rst import directives, Directive
from myst_parser.mdit_to_docutils.base import DocutilsRenderer
from myst_parser.mdit_to_docutils.sphinx_ import SphinxRenderer
from myst_parser.parsers.mdit import create_md_parser
from sphinx import application, addnodes
from sphinx.addnodes import desc_name, desc_signature, desc
from sphinx.directives import ObjectDescription, ObjDescT
from sphinx.directives.patches import Code
from sphinx.domains import Domain, ObjType
from sphinx.roles import XRefRole
from sphinx.util import ws_re
from sphinx.util.docutils import SphinxDirective
from sphinx.util.nodes import make_refnode

try:
    from yaml import CLoader as Loader, CDumper as Dumper, SafeDumper
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


class ActionsItemDirective(ObjectDescription[str], MarkdownParsingMixin):
    index_template: str = '%s'
    fields = []
    option_spec = {'description': directives.unchanged_required}

    @classmethod
    def __init_subclass__(cls, /, **kwargs):
        cls.option_spec |= {f: directives.unchanged_required for f in cls.fields}
        cls.option_spec |= ActionsItemDirective.option_spec

    @classmethod
    def generate(cls, item_name, item_meta, lineno, content_offset, state, state_machine):
        options = {k: str(v) for k, v in item_meta.items() if k in cls.option_spec}
        # noinspection PyTypeChecker
        directive = cls('', [item_name], options, '', lineno, content_offset, "", state, state_machine)
        node = directive.run()
        return node

    def handle_signature(self, sig: str, sig_node) -> str:
        sig_node.clear()
        sig_node += desc_name(sig, sig)
        name = ws_re.sub(' ', sig)
        sig_node['fullname'] = sig
        return name

    def _object_hierarchy_parts(self, sig_node) -> tuple[str, ...]:
        return (sig_node['fullname'],)

    def _toc_entry_name(self, sig_node) -> str:
        if not sig_node.get('_toc_parts'):
            return ''
        name, = sig_node['_toc_parts']
        return name

    def add_target_and_index(self, name: str, sig: str, signode) -> None:
        node_id = sphinx.util.nodes.make_id(self.env, self.state.document, self.objtype, name)
        signode['ids'].append(node_id)
        self.state.document.note_explicit_target(signode)

    def format_field(self, field_name: str, field_value):

        parsed, msgs = self.parse_inline(field_value, lineno=self.lineno)
        value = nodes.literal('', field_value, )
        field = nodes.field(
            '',
            nodes.field_name('', field_name.title()),
            nodes.field_body('', value),
        )
        return field, msgs

    def transform_content(self, content_node) -> None:
        """Insert fields as a field list."""
        field_list = nodes.field_list()
        for field_name in self.fields:
            if field_value := self.options.get(field_name):
                field, msgs = self.format_field(field_name, field_value)
                field_list.append(field)
                field_list += msgs
        if len(field_list.children) > 0:
            content_node.insert(0, field_list)

        if description := self.options.get('description'):
            self.parse_markdown(description, inline=False, node=content_node)


class ActionInputDirective(ActionsItemDirective):
    fields = ['required', 'default']
    option_spec = {'deprecationMessage': directives.unchanged_required}

    def format_deprecationMessage(self, message):
        admonition = nodes.admonition()
        admonition['classes'].append('warning')
        title_text = 'Deprecated'
        textnodes, msg = self.state.inline_text(title_text, self.lineno)
        title = nodes.title(title_text, '', *textnodes)
        title.source, title.line = (
            self.state_machine.get_source_and_line(self.lineno))

        admonition += title
        admonition += msg

        admonition['type'] = 'deprecated'
        admonition.document = self.state.document
        self.parse_markdown(message, inline=True, node=admonition)
        return admonition, []

    def transform_content(self, content_node) -> None:
        super().transform_content(content_node)
        if deprecation_message := self.options.get('deprecationMessage'):
            admonition, msgs = self.format_deprecationMessage(deprecation_message)
            content_node.insert(0, admonition)


class ActionOutputDirective(ActionsItemDirective):
    pass


class ActionDirective(ObjectDescription, MarkdownParsingMixin):
    has_content = True
    final_argument_whitespace = True
    required_arguments = 0
    optional_arguments = 1
    option_spec = {
        'path': directives.unchanged_required,
    }

    def example(self):
        if example_yaml := self.options.get('x-example'):
            return example_yaml

        slug = self.env.config['sphinx_gha_repo_slug']
        if slug is None:
            self.error("No repo slug provided. please set the sphinx_gha_repo_slug config variable")
        action_path = Path(self.action_path).parent
        repo_root = self.env.config['sphinx_gha_repo_root'] or os.getcwd()
        repo_root = Path(repo_root)
        relative_path = str(action_path.relative_to(repo_root))

        if relative_path != '.':
            slug = slug + '/' + relative_path

        action_ref = self.env.config['sphinx_gha_repo_ref']

        if action_ref:
            slug = slug + '@' + action_ref

        name = self.action_yaml.get('x-example-name')
        inputs = self.action_yaml.get('x_example_inputs') or {}
        env = self.action_yaml.get('x_example_env') or {}

        for (k, d, e) in [
            ('x-env', env, 'example'),
            ('inputs', inputs, 'x-example')
        ]:
            if action_inputs := self.action_yaml.get(k):
                for input_name, input_meta in action_inputs.items():
                    input_meta = input_meta or {}
                    if input_example := input_meta.get(e):
                        d[input_name] = input_example

        example_yaml = {}

        if name:
            example_yaml['name'] = name
        example_yaml['using'] = slug
        if inputs:
            example_yaml['with'] = inputs
        if env:
            example_yaml['env'] = env

        example_yaml = [example_yaml]
        return yaml.dump(example_yaml, Dumper=SafeDumper, sort_keys=False)

    def handle_signature(self, sig: str, sig_node: desc_signature) -> ObjDescT:
        sig_node.clear()
        sig_prefix = [nodes.Text('action'), addnodes.desc_sig_space()]
        sig_node += addnodes.desc_annotation(str(sig_prefix), '', *sig_prefix)
        sig_node += desc_name(sig, sig)
        name = ws_re.sub(' ', sig)
        sig_node['fullname'] = sig
        return name

    def get_signatures(self) -> list[str]:
        self.action_path = self.options.get('path')

        if self.action_path:
            action_path = Path(self.env.config['sphinx_gha_repo_root']) / self.action_path
            for filename in ['action.yml', 'action.yaml']:
                test_path = action_path / filename
                if test_path.exists():
                    self.action_path = test_path

        if self.action_path:
            with open(self.action_path, 'rt') as stream:
                self.action_yaml = yaml.full_load(stream)
        else:
            self.action_yaml = {}

        self.action_name = None
        if len(self.arguments) > 0:
            self.action_name = self.arguments[0]
        elif 'name' in self.action_yaml:
            self.action_name = self.action_yaml['name']
        elif self.action_path:
            self.action_name = Path(self.action_path).parent.name
        else:
            self.error('Neither a path nor an action name provided!')

        self.action_id = Path(self.action_path).parent.name if self.action_path else self.action_name

        return [self.action_id]

    def _object_hierarchy_parts(self, sig_node) -> tuple[str, ...]:
        return (sig_node['fullname'],)

    def _toc_entry_name(self, sig_node) -> str:
        if not sig_node.get('_toc_parts'):
            return ''
        name, = sig_node['_toc_parts']
        return name

    def add_target_and_index(self, name: str, sig: str, signode) -> None:
        node_id = sphinx.util.nodes.make_id(self.env, self.state.document, self.objtype, name)
        signode['ids'].append(node_id)
        self.state.document.note_explicit_target(signode)

    def transform_content(self, content_node: addnodes.desc_content) -> None:
        domain_name = self.name.split(':')[0]
        domain_obj = self.env.domains[domain_name]

        if description := self.action_yaml.get('description'):
            content_node.extend(self.parse_markdown(description))

        # Example code

        if example_yaml := self.example():
            code_section = nodes.section(
                '',
                nodes.rubric(text='Example'),
                ids=[nodes.make_id(self.action_id + '_example')],
                names=[nodes.fully_normalize_name('example')],
            )

            code = Code('Code', ['yaml'], {}, content=example_yaml.splitlines(), lineno=self.lineno, content_offset=self.content_offset, block_text='',
                        state=self.state, state_machine=self.state_machine)
            code_section.extend(code.run())
            content_node.append(code_section)

        # Items

        item_lists = [
            ('inputs', 'Inputs', 'action-input'),
            ('outputs', 'Outputs', 'action-output'),
            ('x-env', 'Environment Variables', 'action-input')
        ]

        for (key, title, directive) in item_lists:
            if item_list := self.action_yaml.get(key):
                item_list_section = nodes.section(
                    '',
                    nodes.rubric(text=title),
                    ids=[nodes.make_id(self.action_id + '_' + title)],
                    names=[nodes.fully_normalize_name(title)],
                )
                for item_name, item_meta in item_list.items():
                    if item_meta is None:
                        item_meta = {}
                    item_list_section.extend(
                        domain_obj.directive(directive).generate(item_name, item_meta, self.lineno, self.content_offset, self.state, self.state_machine))
                content_node.append(item_list_section)


class GHActionsDomain(Domain):
    name = 'gh-actions'
    label = 'Github Actions'
    roles = {
        'action': XRefRole(),
    }
    directives = {
        'action': ActionDirective,
        'action-input': ActionInputDirective,
        'action-output': ActionOutputDirective,
    }

    initial_data = {
        'actions': []
    }

    object_types = {
        'action-input': ObjType('action-input')
    }

    def get_full_qualified_name(self, node):
        return f'gh-actions.{node.arguments[0]}'

    def get_objects(self) -> Iterable[tuple[str, str, str, str, str, int]]:
        yield from self.data['actions']

    def resolve_xref(self, env, fromdocname, builder, typ, target, node, contnode):
        match = [
            (docname, anchor) for name, sig, typ, docname, anchor, prio in self.get_objects() if sig == target
        ]

        if len(match) > 0:
            todocname = match[0][0]
            targ = match[0][1]

            return make_refnode(builder, fromdocname, todocname, targ, contnode, targ)

        else:
            print('Awww, found nothing')
            return None

    def add_action(self, signature, full_name):
        """Add a new action to the domain"""
        name = f'action.{signature}'
        anchor = f'action-{signature}'

        self.data['actions'].append((
            name, full_name, 'gh-action', self.env.docname, anchor, 0
        ))


def setup(app: application.Sphinx) -> ty.Dict[str, ty.Any]:
    app.add_domain(GHActionsDomain)
    app.add_config_value('sphinx_gha_repo_ref', os.environ.get('READTHEDOCS_GIT_IDENTIFIER') or 'main', 'env')
    app.add_config_value('sphinx_gha_repo_slug', 'UNKNOWN REPO', 'env')
    app.add_config_value('sphinx_gha_repo_root', os.getcwd(), 'env')
    app.setup_extension('myst_parser')

    return {
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
