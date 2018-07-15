function formatState(state, time)
{   
    dateStr = formatDate(time);
    return state.charAt(0).toUpperCase() + state.slice(1) + " as of " + dateStr;
};

function formatDate(time)
{   
	return dateFormat(new Date(parseInt(time)*1000), "mmm dS, yyyy, h:MM TT");
};

function click(name) 
{
	pl = {"door": name}
	$.ajax({
		url:"toggle",
		type: 'PUT',
		dataType: 'json',
		contentType: "application/json",
		data: JSON.stringify(pl),
    })
};

$.ajax({
    url:"status_all",
    success: function(json) {
		console.log(JSON.stringify(json));
		$.each(json.doors, function(idx, door){
			var id = door.id;
			var name = door.name;
			var state = door.last_state;
			var time = door.last_state_time;
			var sensor = door.sensor_status;
			var li = '<li id="' + id + '" data-icon="false">';
			li = li + '<a href="javascript:click(\'' + id + '\');">';
			li = li + '<img id=' + id + '-img' + ' src="/static/img/'+state + '.png" />';
			li = li + '<h3>' + name + '</h3>';
			li = li + '<p id=' + id + '-time' +'>' + formatState(state, time) + '</p>';
			li = li + '<h4 id=' + id + '-sensor' + '> Sensor current status: ' + sensor + '</h4>';
			li = li + '</a></li>';
			$("#doorlist").append(li);
			$("#doorlist").listview('refresh');
		});
    }
});

function uptime() {
     $.ajax({
 	url:"uptime",
 	success: function(data) {
 	    $("#uptime").html("Server uptime: " + data.uptime);
 	    setTimeout('uptime()', 60000)
 	},
 	error: function(XMLHttpRequest, textStatus, errorThrown) {
 	    setTimeout('uptime()', 60000)
 	},
 	dataType: "json",
 	timeout: 60000	
     });
}


function poll(){
    $.ajax({ 
    	url: "status_all",
    	success: function(json) {
    	    $.each(json.doors, function(idx, door){
				var id = door.id;
				var state = door.last_state;
				var time = door.last_state_time;
				var sensor = door.sensor_status;
				$("#" + id + "-time").html(formatState(state, time));
				$("#" + id + "-img").attr("src", "/static/img/" + state + ".png")
				$("#" + id + "-sensor").html("Sensor current status: " + sensor)
				$("#doorlist").listview('refresh');
    	    });
    	    setTimeout('poll()', 1000);
        },
        // handle error
        error: function(XMLHttpRequest, textStatus, errorThrown){
            // try again in 10 seconds if there was a request error
            setTimeout('poll();', 10000);
        },
    	//complete: poll,
    	dataType: "json", 
    	timeout: 30000
    });    
};

function init() {
    uptime()
    poll()
}

$(document).live('pageinit', init);
