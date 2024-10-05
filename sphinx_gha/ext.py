from __future__ import annotations

import logging
import os
import typing as ty
from abc import abstractmethod
from functools import cached_property
from pathlib import Path
from typing import Iterable, Optional

import sphinx
import yaml
from docutils import nodes
from docutils.nodes import Element, section
from docutils.parsers.rst import directives, Directive
from myst_parser.mdit_to_docutils.base import DocutilsRenderer
from myst_parser.mdit_to_docutils.sphinx_ import SphinxRenderer
from myst_parser.parsers.mdit import create_md_parser
from sphinx import application, addnodes
from sphinx.addnodes import desc_name, desc_signature, pending_xref
from sphinx.builders import Builder
from sphinx.directives import ObjectDescription, ObjDescT
from sphinx.directives.patches import Code
from sphinx.domains import Domain, ObjType
from sphinx.domains.std import StandardDomain
from sphinx.environment import BuildEnvironment
from sphinx.roles import XRefRole
from sphinx.util import ws_re
from sphinx.util.nodes import make_refnode, make_id, find_pending_xref_condition

try:
    from yaml import CLoader as Loader, CDumper as Dumper, SafeDumper
except ImportError:
    from yaml import Loader, Dumper, SafeDumper

logger = logging.getLogger(__name__)


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
    parent_role = ''
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
        parent = self.env.ref_context.get(self.parent_role)
        name = ws_re.sub(' ', sig)

        sig_node.clear()
        sig_node += desc_name(sig, sig)
        sig_node['name'] = sig
        if parent:
            sig_node['parent'] = parent
            sig_node['fullname'] = parent + '.' + sig
        else:
            sig_node['fullname'] = sig
        return name

    def _object_hierarchy_parts(self, sig_node) -> tuple[str, ...]:
        return tuple(sig_node['fullname'].split('.'))

    def _toc_entry_name(self, sig_node) -> str:
        return sig_node['name']

    def add_target_and_index(self, name: str, sig: str, sig_node) -> None:
        node_id = sphinx.util.nodes.make_id(self.env, self.state.document, self.objtype, name)
        sig_node['ids'].append(node_id)
        self.state.document.note_explicit_target(sig_node)

        domain = self.env.domains['gh-actions']
        domain.note_object(sig_node['fullname'], sig_node['name'], self.objtype, node_id)

    def format_field(self, field_name: str, field_value):
        parsed, msgs = self.parse_inline(field_value, lineno=self.lineno)
        value = nodes.literal('', field_value, )
        field = nodes.field(
            '',
            nodes.field_name('', field_name.title()),
            nodes.field_body('', value),
        )
        return field, msgs

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
    parent_role = 'gh-actions:action'
    fields = ['required', 'default']
    option_spec = {'deprecationMessage': directives.unchanged_required}

    def transform_content(self, content_node) -> None:
        super().transform_content(content_node)
        if deprecation_message := self.options.get('deprecationMessage'):
            admonition, msgs = self.format_deprecationMessage(deprecation_message)
            content_node.insert(0, admonition)


class ActionOutputDirective(ActionsItemDirective):
    parent_role = 'gh-actions:action'


class ActionEnvDirective(ActionsItemDirective):
    parent_role = 'gh-actions:action'
    fields = ['required']

    def add_target_and_index(self, name: str, sig: str, signode) -> None:
        super().add_target_and_index(name, sig, signode)
        objtype = 'envvar'
        node_id = make_id(self.env, self.state.document, objtype, name)
        signode['ids'].append(node_id)

        std: StandardDomain = self.env.domains['std']
        std.note_object(objtype, signode['fullname'], node_id, location=signode)


class ActionsFileDirective(ObjectDescription, MarkdownParsingMixin):
    role = None
    file_type = None
    has_content = True
    final_argument_whitespace = True
    required_arguments = 0
    optional_arguments = 1
    option_spec = {
        'path': directives.unchanged_required,
    }

    @classmethod
    def id_from_path(cls, path: Path) -> str:
        if path is None:
            raise ValueError('path cannot be None')
        if (path_stem := path.stem) is not None:
            return path_stem
        else:
            raise ValueError('path stem cannot be None')

    @cached_property
    def id(self) -> str:
        if len(self.arguments) > 0:
            assert self.arguments[0] is not None
            return self.arguments[0]
        elif (path := self.path) is not None:
            return self.id_from_path(path)
        else:
            self.error('Neither a path nor name provided!')

    @cached_property
    def path(self) -> Optional[Path]:
        if (path := self.options.get('path')) is not None:
            repo_root = Path(self.env.config['sphinx_gha_repo_root'] or os.getcwd()).absolute()
            return repo_root / Path(path)
        else:
            return None

    @cached_property
    def yaml(self) -> dict:
        path = self.path
        if path is None:
            return {}
        with open(path, 'rt') as stream:
            return yaml.full_load(stream)

    @cached_property
    def example(self) -> str:
        return ''

    @property
    def domain_obj(self) -> GHActionsDomain:
        domain_name = self.name.split(':')[0]
        return self.env.domains[domain_name]

    def get_signatures(self) -> list[str]:
        return [self.id]

    def handle_signature(self, sig: str, sig_node: desc_signature) -> ObjDescT:
        if sig is None:
            raise ValueError('sig cannot be None')
        self.env.ref_context[self.role] = self.id
        sig_node.clear()
        sig_prefix = [nodes.Text(self.file_type), addnodes.desc_sig_space()]
        sig_node += addnodes.desc_annotation(str(sig_prefix), '', *sig_prefix)
        sig_node += desc_name(sig, sig)
        name = ws_re.sub(' ', sig)
        sig_node['fullname'] = sig
        sig_node['name'] = sig
        return name

    def _object_hierarchy_parts(self, sig_node) -> tuple[str, ...]:
        return (self.id,)

    def _toc_entry_name(self, sig_node) -> str:
        if not sig_node.get('_toc_parts'):
            return ''
        name, = sig_node['_toc_parts']
        return name

    def add_target_and_index(self, name: str, sig: str, sig_node) -> None:
        node_id = sphinx.util.nodes.make_id(self.env, self.state.document, self.objtype, name)
        sig_node['ids'].append(node_id)
        self.state.document.note_explicit_target(sig_node)
        self.domain_obj.note_object(sig_node['fullname'], sig_node['name'], self.objtype, node_id)

    def transform_content(self, content_node: addnodes.desc_content) -> None:
        if description := self.yaml.get('description'):
            content_node.extend(self.parse_markdown(description))

            # Example code

        if example_yaml := self.example:
            code_section = nodes.section(
                '',
                nodes.rubric(text='Example'),
                ids=[nodes.make_id(self.id + '_example')],
                names=[nodes.fully_normalize_name('example')],
            )

            code = Code('Code', ['yaml'], {}, content=example_yaml.splitlines(), lineno=self.lineno, content_offset=self.content_offset, block_text='',
                        state=self.state, state_machine=self.state_machine)
            code_section.extend(code.run())
            content_node.append(code_section)

    def format_item_list(self, title, directive, items) -> section:
        item_list_section = nodes.section(
            '',
            nodes.rubric(text=title),
            ids=[nodes.make_id(self.id + '_' + title)],
            names=[nodes.fully_normalize_name(title)],
        )
        for item_name, item_meta in items.items():
            if item_meta is None:
                item_meta = {}
            directive_obj = self.domain_obj.directive(directive)
            item_nodes = directive_obj.generate(item_name, item_meta,
                                                self.lineno, self.content_offset,
                                                self.state, self.state_machine)
            item_list_section.extend(item_nodes)
        return item_list_section


class ActionDirective(ActionsFileDirective):
    role = 'gh-actions:action'
    file_type = 'action'

    @cached_property
    def path(self):
        path = self.options.get('path')
        if path is None:
            return None
        repo_root = Path(self.env.config['sphinx_gha_repo_root'] or os.getcwd()).absolute()
        path = repo_root / Path(path)
        for filename in ['action.yml', 'action.yaml']:
            test_path = path / filename
            if test_path.exists():
                return test_path
        if path.is_file():
            return path

        self.error(f'Could not find an action definition at {path}')

    @classmethod
    def id_from_path(cls, path: Path):
        return path.parent.name

    @cached_property
    def example(self):
        if example_yaml := self.options.get('x-example'):
            return example_yaml

        if self.path is None:
            return ''

        slug = self.env.config['sphinx_gha_repo_slug']
        if slug is None:
            self.error("No repo slug provided. please set the sphinx_gha_repo_slug config variable")
        action_path = self.path.parent.absolute()
        repo_root = Path(self.env.config['sphinx_gha_repo_root'] or os.getcwd()).absolute()
        relative_path = str(action_path.relative_to(repo_root))

        if relative_path != '.':
            slug = slug + '/' + relative_path

        action_ref = self.env.config['sphinx_gha_repo_ref']

        if action_ref:
            slug = slug + '@' + action_ref

        name = self.yaml.get('x-example-name')
        inputs = self.yaml.get('x_example_inputs') or {}
        env = self.yaml.get('x_example_env') or {}

        for (k, d, e) in [
            ('x-env', env, 'example'),
            ('inputs', inputs, 'x-example')
        ]:
            if action_inputs := self.yaml.get(k):
                for input_name, input_meta in action_inputs.items():
                    input_meta = input_meta or {}
                    if input_example := input_meta.get(e):
                        d[input_name] = input_example

        example_yaml = {}

        if name:
            example_yaml['name'] = name
        example_yaml['uses'] = slug
        if inputs:
            example_yaml['with'] = inputs
        if env:
            example_yaml['env'] = env

        example_yaml = [example_yaml]
        return yaml.dump(example_yaml, Dumper=SafeDumper, sort_keys=False)

    def transform_content(self, content_node: addnodes.desc_content) -> None:
        super().transform_content(content_node)

        # Items

        item_lists = [
            ('inputs', 'Inputs', 'action-input'),
            ('outputs', 'Outputs', 'action-output'),
            ('x-env', 'Environment Variables', 'action-envvar')
        ]

        for (key, title, directive) in item_lists:
            if item_list := self.yaml.get(key):
                content_node.append(self.format_item_list(title, directive, item_list))


class WorkflowInputDirective(ActionsItemDirective):
    parent_role = 'gh-actions:workflow'
    fields = ['required', 'default', 'type']


class WorkflowSecretDirective(WorkflowInputDirective):
    pass


class WorkflowOutputDirective(ActionsItemDirective):
    parent_role = 'gh-actions:workflow'


class WorkflowDirective(ActionsFileDirective):
    role = 'gh-actions:workflow'
    file_type = 'workflow'

    def transform_content(self, content_node: addnodes.desc_content) -> None:
        super().transform_content(content_node)
        # Items

        item_lists = [
            ('inputs', 'Inputs', 'workflow-input'),
            ('secrets', 'Secrets', 'workflow-secret'),
            ('outputs', 'Outputs', 'workflow-output'),
        ]

        if (on_node := self.yaml.get('on') or self.yaml.get(True)) is None:
            # fucking yaml parses `on` as a boolean even in keys what the fuck
            return self.error(f'Workflow {self.path} has no `on` node')
        if (call_node := on_node.get('workflow_call')) is None:
            return self.error(f'Workflow {self.path} is not callable')

        for (key, title, directive) in item_lists:
            if item_list := call_node.get(key):
                content_node.append(self.format_item_list(title, directive, item_list))


class GHActionsDomain(Domain):
    name = 'gh-actions'
    label = 'Github Actions'
    directives = {
        'action': ActionDirective,
        'action-input': ActionInputDirective,
        'action-output': ActionOutputDirective,
        'action-envvar': ActionEnvDirective,
        'workflow': WorkflowDirective,
        'workflow-input': WorkflowInputDirective,
        'workflow-secret': WorkflowSecretDirective,
        'workflow-output': WorkflowOutputDirective,
    }
    roles = {directive: XRefRole() for directive in directives}
    object_types = {role: ObjType(role) for role in roles.keys()}

    initial_data = {
        'objects': []
    }

    def get_full_qualified_name(self, node):
        parent_name = node.get('gh-actions:action') or node.get('gh-actions:workflow')
        target = node.get('reftarget')
        if target is None:
            return None
        else:
            return '.'.join(filter(None, [parent_name, target]))

    def get_objects(self) -> Iterable[tuple[str, str, str, str, str, int]]:
        yield from self.data['objects']

    def find_obj(self, env: BuildEnvironment, modname: str, classname: str,
                 name: str, ty: str | None, searchmode: int = 0, ) -> list[tuple[str,]]:
        pass

    def resolve_xref(self, env: BuildEnvironment, fromdocname: str, builder: Builder,
                     typ: str, target: str, node: pending_xref, contnode: Element,
                     ) -> Element | None:
        typ_name = typ.split(':')[-1]
        dispname = target
        if len(target.split('.')) == 1:
            # figure out the parent object from the current context
            if typ_name.startswith('action-'):
                parent_name = node.get('gh-actions:action')
            elif typ_name.startswith('workflow-'):
                parent_name = node.get('gh-actions:workflow')
            else:
                parent_name = None
            target = '.'.join(filter(None, [parent_name, target]))  # extend target full name with parent

        matches = [
            (docname, anchor) for name, dispname, objtyp, docname, anchor, prio in self.get_objects() if name == target and objtyp == typ
        ]

        if not matches:
            return None

        if len(matches) > 1:
            logger.warning('more than one target found for cross-reference %r: %s',
                           target, ', '.join(match[0] for match in matches))

        docname, anchor = matches[0]

        # determine the content of the reference by conditions
        content = find_pending_xref_condition(node, 'resolved')
        if content:
            children = content.children
        else:
            # if not found, use contnode
            children = [contnode]

        return make_refnode(builder, fromdocname, docname, anchor, children, title=dispname)

    def resolve_any_xref(self, env: BuildEnvironment, fromdocname: str, builder: Builder,
                         target: str, node: pending_xref, contnode: Element,
                         ) -> list[tuple[str, Element]]:
        matches = []
        for typ in self.object_types.keys():
            match = self.resolve_xref(env, fromdocname, builder, typ, target, node, contnode)
            if match is not None:
                matches.append((typ, match))
        return matches

    def note_object(self, name: str, dispname: str, typ: str, anchor: str) -> None:
        """Note a python object for cross reference. """
        self.data['objects'].append((name, dispname, typ, self.env.docname, anchor, 1))


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
