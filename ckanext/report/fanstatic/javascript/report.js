$(document).ready(function () {
        $(".js-auto-submit").change(function () {
            $(this).closest("form").submit();
        });

        $(".js-btn-summit").on("click", function () {
            $(this).closest("form").submit();
        });

        var table = $('#report-table');
        table.DataTable({
            searching: false,
            language: {
                url: 'https://cdn.datatables.net/plug-ins/1.10.19/i18n/Spanish.json'
            }
        });
    }
);
