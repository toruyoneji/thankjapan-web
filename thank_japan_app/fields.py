from cloudinary.models import CloudinaryField


class OptimizedCloudinaryField(CloudinaryField):
    """
    Drop-in replacement for CloudinaryField whose `.url` always requests
    f_auto,q_auto (automatic format/quality) from Cloudinary, instead of
    serving the original uploaded file as-is. This applies everywhere the
    field's `.url` is read (templates, serializers, etc.) without needing
    to add transformation params at each call site.
    """
    DELIVERY_OPTIONS = {'fetch_format': 'auto', 'quality': 'auto'}

    def parse_cloudinary_resource(self, value):
        resource = super().parse_cloudinary_resource(value)
        resource.url_options = dict(self.DELIVERY_OPTIONS)
        return resource

    def pre_save(self, model_instance, add):
        value = super().pre_save(model_instance, add)
        resource = getattr(model_instance, self.attname)
        if hasattr(resource, 'url_options'):
            resource.url_options = dict(self.DELIVERY_OPTIONS)
        return value
