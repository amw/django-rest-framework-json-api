"""
Renderers
"""

from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from django.db.models import Manager
from django.template import loader
from django.utils.encoding import force_str
from rest_framework import relations, renderers
from rest_framework.fields import SkipField, get_attribute
from rest_framework.relations import PKOnlyObject
from rest_framework.settings import api_settings

from rest_framework_json_api.relations import (
    HyperlinkedMixin,
    ManySerializerMethodResourceRelatedField,
    ResourceRelatedField,
    SkipDataMixin,
)
from rest_framework_json_api.utils import (
    format_errors,
    format_field_name,
    format_field_names,
    get_included_resources,
    get_related_resource_type,
    get_relation_instance,
    get_resource_id,
    get_resource_name,
    get_resource_type_from_instance,
    get_resource_type_from_serializer,
    get_serializer_fields,
    is_relationship_field,
)


@dataclass
class IncludedCache:
    by_id: dict[tuple[str, str], Any] = dataclass_field(default_factory=dict)

    def add(self, resource):
        resource_type = resource.get("type")
        id = resource.get("id")
        if resource_type is None or id is None:
            return
        self.by_id[(resource_type, id)] = resource

    def check(self, resource):
        resource_type = resource.get("type")
        id = resource.get("id")
        return self.get(resource_type, id) is not None

    def get(self, resource_type, id):
        return self.by_id.get((resource_type, id))

    def remove(self, resource):
        """Used for deduplicating resources already present in main `data`."""
        resource_type = resource.get("type")
        id = resource.get("id")
        key = (resource_type, id)
        if key in self.by_id:
            del self.by_id[key]


class JSONRenderer(renderers.JSONRenderer):
    """
    The `JSONRenderer` exposes a number of methods that you may override if you need highly
    custom rendering control.

    Render a JSON response per the JSON:API spec:

    .. code-block:: json

        {
          "data": [
            {
              "type": "companies",
              "id": "1",
              "attributes": {
                "name": "Mozilla",
                "slug": "mozilla",
                "date-created": "2014-03-13 16:33:37"
              }
            }
          ]
        }
    """

    media_type = "application/vnd.api+json"
    format = "vnd.api+json"

    @classmethod
    def extract_attributes(cls, fields, resource):
        """
        Builds the `attributes` object of the JSON:API resource object.

        Ensures that ID which is always provided in a JSON:API resource object
        and relationships are not returned.
        """

        return {
            format_field_name(field_name): value
            for field_name, value in resource.items()
            if field_name in fields
            and field_name != "id"
            and not is_relationship_field(fields[field_name])
        }

    @classmethod
    def extract_relationships(
        cls, serializer, fields, resource, instance, included_resources, included_cache
    ):
        """
        Builds the relationships top level object based on related serializers.
        Also extracts included resources in a single pass through the fields.
        """
        data = {}

        # Don't try to extract relationships from a non-existent resource
        if instance is None:
            return

        included_serializers = getattr(serializer, "included_serializers", dict())

        for field_name, field in iter(fields.items()):
            # Skip URL field
            if field_name == api_settings.URL_FIELD_NAME:
                continue

            # don't output a key for write only fields
            if fields[field_name].write_only:
                continue

            # Skip fields without relations
            if not is_relationship_field(field):
                continue

            source = field.source
            relation_type = get_related_resource_type(field)
            relation_instance = None
            relation_data = None
            relation_meta = None
            relation_links = None

            if isinstance(field, relations.HyperlinkedIdentityField):
                resolved, relation_instance = get_relation_instance(
                    instance, source, field.parent
                )
                if not resolved:
                    continue

                # Don't try to query an empty relation
                relation_queryset = (
                    relation_instance if relation_instance is not None else list()
                )

                relation_data = [
                    {"type": relation_type, "id": force_str(related_object.pk)}
                    for related_object in relation_queryset
                ]
                relation_links = {"related": resource.get(field_name)}
                relation_meta = {"count": len(relation_data)}

            elif isinstance(field, HyperlinkedMixin):
                relation_links = field.get_links(
                    instance, field.related_link_lookup_field
                )

                if isinstance(field, (ResourceRelatedField,)):
                    if not isinstance(field, SkipDataMixin):
                        relation_data = resource.get(field_name)

                        if isinstance(field, ManySerializerMethodResourceRelatedField):
                            relation_meta = {"count": len(resource.get(field_name))}

            elif isinstance(
                field,
                (relations.PrimaryKeyRelatedField, relations.HyperlinkedRelatedField),
            ):
                resolved, relation = get_relation_instance(
                    instance, f"{source}_id", field.parent
                )
                if not resolved:
                    continue
                relation_id = relation if resource.get(field_name) else None
                if relation_id is not None:
                    relation_data = {
                        "type": relation_type,
                        "id": force_str(relation_id),
                    }

                if isinstance(
                    field, relations.HyperlinkedRelatedField
                ) and resource.get(field_name):
                    relation_links = {"related": resource.get(field_name)}

            elif isinstance(field, relations.ManyRelatedField):
                resolved, relation_instance = get_relation_instance(
                    instance, source, field.parent
                )
                if not resolved:
                    continue

                if isinstance(field.child_relation, HyperlinkedMixin):
                    if isinstance(resource.get(field_name), Iterable):
                        relation_meta = {"count": len(resource.get(field_name))}

                    if isinstance(field.child_relation, ResourceRelatedField):
                        # special case for ResourceRelatedField
                        relation_data = resource.get(field_name)

                    relation_links = field.child_relation.get_links(
                        instance,
                        field.child_relation.related_link_lookup_field,
                    )
                else:
                    relation_data = list()
                    for nested_resource_instance in relation_instance:
                        nested_resource_instance_type = (
                            relation_type
                            or get_resource_type_from_instance(nested_resource_instance)
                        )

                        relation_data.append(
                            {
                                "type": nested_resource_instance_type,
                                "id": force_str(nested_resource_instance.pk),
                            }
                        )
                    relation_meta = {"count": len(relation_data)}

            data[field_name] = {}
            if relation_meta:
                data[field_name]["meta"] = relation_meta
            if not isinstance(field, SkipDataMixin):
                data[field_name]["data"] = relation_data
            if relation_links:
                data[field_name]["links"] = relation_links

            if relation_data and cls.should_include(included_resources, field_name):
                many = isinstance(relation_data, list)
                if many:
                    if all(included_cache.check(d) for d in relation_data):
                        continue
                elif included_cache.check(relation_data):
                    continue

                if relation_instance is None:
                    relation_instance = cls.extract_relation_instance(field, instance)
                    if relation_instance is None:
                        continue

                serializer_class = included_serializers[field_name]

                cls.extract_included(
                    relation_instance,
                    cls.unprefix_included_for_field(included_resources, field_name),
                    included_cache,
                    serializer_class,
                    many,
                    serializer.context,
                )

        return format_field_names(data)

    @classmethod
    def should_include(cls, included_resources, field_name):
        return field_name in included_resources or any(
            n.startswith(f"{field_name}.") for n in included_resources
        )

    @classmethod
    def extract_relation_instance(cls, field, resource_instance):
        """
        Determines what instance represents given relation and extracts it.

        Relation instance is determined exactly same way as it determined
        in parent serializer
        """
        try:
            res = field.get_attribute(resource_instance)
            if isinstance(res, PKOnlyObject):
                return get_attribute(resource_instance, field.source_attrs)
            return res
        except SkipField:
            return None

    @classmethod
    def unprefix_included_for_field(cls, included_resources, field_name):
        prefix = f"{field_name}."
        return [
            key[len(prefix) :] for key in included_resources if key.startswith(prefix)
        ]

    @classmethod
    def extract_included(
        cls,
        relation_instance,
        included_resources,
        included_cache,
        serializer_class,
        many,
        context,
    ):
        if isinstance(relation_instance, Manager):
            # TODO Does this ever happen? It's not covered by tests.
            relation_instance = list(relation_instance.all())
        elif many:
            relation_instance = list(relation_instance)

        serializer = serializer_class(relation_instance, many=many, context=context)
        serializer_data = serializer.data

        if not serializer_data:
            return

        relation_type = get_resource_type_from_serializer(serializer_class)

        cls.convert_serializer_data_to_json_api(
            serializer,
            serializer_data,
            relation_type,
            included_cache,
            included_resources,
        )

    @classmethod
    def extract_meta(cls, serializer, resource):
        """
        Gathers the data from serializer fields specified in meta_fields and adds it to
        the meta object.
        """
        if hasattr(serializer, "child"):
            meta = getattr(serializer.child, "Meta", None)
        else:
            meta = getattr(serializer, "Meta", None)
        meta_fields = getattr(meta, "meta_fields", [])
        data = {}
        for field_name in meta_fields:
            if field_name in resource:
                data[field_name] = resource[field_name]
        return data

    @classmethod
    def extract_root_meta(cls, serializer, resource):
        """
        Calls a `get_root_meta` function on a serializer, if it exists.
        """
        many = False
        if hasattr(serializer, "child"):
            many = True
            serializer = serializer.child

        data = {}
        if getattr(serializer, "get_root_meta", None):
            json_api_meta = serializer.get_root_meta(resource, many)
            assert isinstance(json_api_meta, dict), "get_root_meta must return a dict"
            data.update(json_api_meta)
        return data

    @classmethod
    def _filter_sparse_fields(cls, serializer, fields, resource_name):
        request = serializer.context.get("request")
        if request:
            sparse_fieldset_query_param = f"fields[{resource_name}]"
            sparse_fieldset_value = request.query_params.get(
                sparse_fieldset_query_param
            )
            if sparse_fieldset_value is not None:
                sparse_fields = sparse_fieldset_value.split(",")
                return {
                    field_name: field
                    for field_name, field, in fields.items()
                    if field.field_name in sparse_fields
                    # URL field is not considered a field in JSON:API spec
                    # but a link so need to keep it
                    or (
                        field.field_name == api_settings.URL_FIELD_NAME
                        and isinstance(field, relations.HyperlinkedIdentityField)
                    )
                }

        return fields

    @classmethod
    def build_json_resource_obj(
        cls,
        fields,
        resource,
        resource_id,
        resource_name,
        instance,
        serializer,
        included_resources,
        included_cache,
    ):
        """
        Builds the resource object (type, id, attributes) and extracts relationships.
        """
        resource_data = {
            "type": resource_name,
            "id": resource_id,
        }

        # TODO remove this filter by rewriting extract_relationships
        # so it uses the serialized data as a basis
        fields = cls._filter_sparse_fields(serializer, fields, resource_name)
        attributes = cls.extract_attributes(fields, resource)
        if attributes:
            resource_data["attributes"] = attributes
        relationships = cls.extract_relationships(
            serializer, fields, resource, instance, included_resources, included_cache
        )
        if relationships:
            resource_data["relationships"] = relationships
        # Add 'self' link if field is present and valid
        if api_settings.URL_FIELD_NAME in resource and isinstance(
            fields[api_settings.URL_FIELD_NAME], relations.HyperlinkedIdentityField
        ):
            resource_data["links"] = {"self": resource[api_settings.URL_FIELD_NAME]}

        meta = cls.extract_meta(serializer, resource)
        if meta:
            resource_data["meta"] = format_field_names(meta)

        return resource_data

    def render_relationship_view(
        self, data, accepted_media_type=None, renderer_context=None
    ):
        # Special case for RelationshipView
        view = renderer_context.get("view", None)
        render_data = {"data": data}
        links = view.get_links()
        if links:
            render_data["links"] = links
        return super().render(render_data, accepted_media_type, renderer_context)

    def render_errors(self, data, accepted_media_type=None, renderer_context=None):
        return super().render(
            format_errors(data), accepted_media_type, renderer_context
        )

    def render(self, data, accepted_media_type=None, renderer_context=None):
        renderer_context = renderer_context or {}

        view = renderer_context.get("view", None)
        request = renderer_context.get("request", None)

        # Get the resource name.
        resource_name = get_resource_name(renderer_context)

        # If this is an error response, skip the rest.
        if resource_name == "errors":
            return self.render_errors(data, accepted_media_type, renderer_context)

        # if response.status_code is 204 then the data to be rendered must
        # be None
        response = renderer_context.get("response", None)
        if response is not None and response.status_code == 204:
            return super().render(None, accepted_media_type, renderer_context)

        from rest_framework_json_api.views import RelationshipView

        if isinstance(view, RelationshipView):
            return self.render_relationship_view(
                data, accepted_media_type, renderer_context
            )

        # If `resource_name` is set to None then render default as the dev
        # wants to build the output format manually.
        if resource_name is None or resource_name is False:
            return super().render(data, accepted_media_type, renderer_context)

        json_api_data = data
        # initialize json_api_meta with pagination meta or an empty dict
        json_api_meta = data.get("meta", {}) if isinstance(data, dict) else {}
        included_cache = IncludedCache()

        if data and "results" in data:
            serializer_data = data["results"]
        else:
            serializer_data = data

        serializer = getattr(serializer_data, "serializer", None)

        included_resources = get_included_resources(request, serializer)

        if serializer is not None:
            # Extract root meta for any type of serializer
            json_api_meta.update(self.extract_root_meta(serializer, serializer_data))
            json_api_data = self.convert_serializer_data_to_json_api(
                serializer,
                serializer_data,
                resource_name,
                included_cache,
                included_resources,
            )

        # Make sure we render data in a specific order
        render_data = {}

        if isinstance(data, dict) and data.get("links"):
            render_data["links"] = data.get("links")

        # format the api root link list
        if view.__class__ and view.__class__.__name__ == "APIRoot":
            render_data["data"] = None
            render_data["links"] = json_api_data
        else:
            render_data["data"] = json_api_data

        if included_resources:
            render_data["included"] = list()

        if included_cache.by_id:
            if isinstance(json_api_data, list):
                objects = json_api_data
            else:
                objects = [json_api_data]

            for obj in objects:
                included_cache.remove(obj)

            if included_cache.by_id:
                render_data["included"] = [
                    included_cache.by_id[k] for k in sorted(included_cache.by_id)
                ]

        if json_api_meta:
            render_data["meta"] = format_field_names(json_api_meta)

        return super().render(render_data, accepted_media_type, renderer_context)

    @classmethod
    def convert_serializer_data_to_json_api(
        cls,
        serializer,
        serializer_data,
        resource_name,
        included_cache,
        included_resources,
        use_from_cache=True,
    ):
        json_api_data = list()
        many = getattr(serializer, "many", False)

        if many:
            serializer_instance = serializer.instance
            nested_serializer = serializer.child
            context = serializer.child.context
            is_polymorphic_serializer = getattr(
                serializer.child, "_poly_force_type_resolution", False
            )
            if not is_polymorphic_serializer:
                fields = get_serializer_fields(serializer.child)
        else:
            serializer_instance = [serializer.instance]
            serializer_data = [serializer_data]
            nested_serializer = serializer
            fields = get_serializer_fields(serializer)
            is_polymorphic_serializer = getattr(
                serializer, "_poly_force_type_resolution", False
            )

        if is_polymorphic_serializer:
            resource_name = None  # force resolving the name from instance

        for position in range(len(serializer_data)):
            resource = serializer_data[position]
            resource_instance = serializer_instance[position]

            if many and is_polymorphic_serializer:
                resource_serializer_class = (
                    serializer.child.get_polymorphic_serializer_for_instance(
                        resource_instance
                    )
                )
                nested_serializer = resource_serializer_class(
                    resource_instance,
                    context=context,
                )
                fields = get_serializer_fields(nested_serializer)

            resolved_resource_name = resource_name or get_resource_type_from_instance(
                resource_instance
            )
            resource_id = get_resource_id(resource_instance, resource)

            if use_from_cache and (
                already_built := included_cache.get(resolved_resource_name, resource_id)
            ):
                json_api_data.append(already_built)
            else:
                json_resource_obj = cls.build_json_resource_obj(
                    fields,
                    resource,
                    resource_id,
                    resolved_resource_name,
                    resource_instance,
                    nested_serializer,
                    included_resources,
                    included_cache,
                )
                json_api_data.append(json_resource_obj)
                included_cache.add(json_resource_obj)

        return json_api_data if many else json_api_data[0]


class BrowsableAPIRenderer(renderers.BrowsableAPIRenderer):
    template = "rest_framework_json_api/api.html"
    includes_template = "rest_framework_json_api/includes.html"

    def get_context(self, data, accepted_media_type, renderer_context):
        context = super().get_context(data, accepted_media_type, renderer_context)
        view = renderer_context["view"]

        context["includes_form"] = self.get_includes_form(view)

        return context

    @classmethod
    def _get_included_serializers(cls, serializer, prefix="", already_seen=None):
        if not already_seen:
            already_seen = set()

        if serializer in already_seen:
            return []

        included_serializers = []
        already_seen.add(serializer)

        for include, included_serializer in getattr(
            serializer, "included_serializers", dict()
        ).items():
            included_serializers.append(f"{prefix}{include}")
            included_serializers.extend(
                cls._get_included_serializers(
                    included_serializer,
                    f"{prefix}{include}.",
                    already_seen=already_seen,
                )
            )

        return included_serializers

    def get_includes_form(self, view):
        try:
            if "related_field" in view.kwargs:
                serializer_class = view.get_related_serializer_class()
            else:
                serializer_class = view.get_serializer_class()
        except AttributeError:
            return
        if not hasattr(serializer_class, "included_serializers"):
            return

        template = loader.get_template(self.includes_template)
        context = {"elements": self._get_included_serializers(serializer_class)}
        return template.render(context)
